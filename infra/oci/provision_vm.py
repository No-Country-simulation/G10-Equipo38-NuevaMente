"""#47: receta OCI A1 gratuita; consulta por defecto, --apply crea recursos."""

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import oci

SHAPE = "VM.Standard.A1.Flex"
OCPUS, MEMORY_GB, BOOT_GB = 2, 12, 50
TAG = {"project": "NuevaMente", "issue": "47"}
NONE_RETRY = oci.retry.NoneRetryStrategy()


class Blocked(RuntimeError):
    """Falta una condición necesaria; no usar alternativas pagas."""


def admin_address(value):
    net = ipaddress.ip_network(value, strict=True)
    if net.version != 4 or net.prefixlen != 32 or not net.network_address.is_global:
        raise Blocked("SSH requiere una IPv4 pública de administración con /32")
    return str(net)


def public_key(path):
    parts = Path(path).read_text(encoding="utf-8").strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise Blocked("Se requiere la clave pública OpenSSH Ed25519 (.pub)")
    raw = base64.b64decode(parts[1], validate=True)
    if len(raw) != 51 or not raw.startswith(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"):
        raise Blocked("Clave pública Ed25519 inválida")
    return " ".join(parts[:2])


def check_allocation(instances, volumes, existing_id=None):
    active = [i for i in instances if i.lifecycle_state != "TERMINATED"]
    a1 = [i for i in active if i.shape == SHAPE]
    extra = 0 if any(i.id == existing_id for i in a1) else 1
    cpu = sum(i.shape_config.ocpus for i in a1)
    ram = sum(i.shape_config.memory_in_gbs for i in a1)
    disk = sum(v.size_in_gbs for v in volumes if v.lifecycle_state != "TERMINATED")
    if cpu + extra * OCPUS > 2 or ram + extra * MEMORY_GB > 12 or disk + extra * BOOT_GB > 200:
        raise Blocked("No queda asignación Always Free suficiente; no crear otra instancia/disco")
    return {"a1_ocpus_used": cpu, "a1_memory_gb_used": ram, "boot_block_gb_used": disk}


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class Infrastructure:
    def __init__(self, config, compartment):
        self.config, self.compartment = config, compartment
        options = {"retry_strategy": NONE_RETRY, "timeout": (5, 30)}
        self.identity = oci.identity.IdentityClient(config, **options)
        self.compute = oci.core.ComputeClient(config, **options)
        self.block = oci.core.BlockstorageClient(config, **options)
        self.network = oci.core.VirtualNetworkClient(config, **options)
        self.limits = oci.limits.LimitsClient(config, **options)
        self.requests = 0

    def call(self, method, *args, **kwargs):
        for attempt in range(3):
            self.requests += 1
            if self.requests > 256:
                raise Blocked("Límite de consultas de esta ejecución alcanzado; revisar y retomar manualmente")
            try:
                return method(*args, retry_strategy=NONE_RETRY, **kwargs)
            except oci.exceptions.ServiceError as error:
                capacity = "out of host capacity" in error.message.lower()
                if capacity or error.status not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise
                time.sleep(attempt + 1)
            except (oci.exceptions.RequestException, oci.exceptions.ConnectTimeout):
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)

    def listing(self, method, *args, **kwargs):
        result, page = [], None
        while True:
            response = self.call(method, *args, **kwargs, **({"page": page} if page else {}))
            result.extend(response.data)
            page = response.headers.get("opc-next-page")
            if not page:
                return result

    def preflight(self, state=None):
        tenant = self.call(self.identity.get_tenancy, self.config["tenancy"]).data
        regions = self.listing(self.identity.list_regions)
        home = next(r.name for r in regions if r.key == tenant.home_region_key)
        if self.config["region"] != home:
            raise Blocked("La configuración debe apuntar a la home region")
        compartments = self.listing(
            self.identity.list_compartments, tenant.id, compartment_id_in_subtree=True, access_level="ANY"
        )
        ids = [tenant.id] + [c.id for c in compartments if c.lifecycle_state == "ACTIVE"]
        if self.compartment not in ids:
            raise Blocked("El compartimento no pertenece al inventario accesible de esta tenancy")
        ads = self.listing(self.identity.list_availability_domains, tenant.id)
        instances, volumes = [], []
        for compartment in ids:
            instances.extend(self.listing(self.compute.list_instances, compartment))
            volumes.extend(self.listing(self.block.list_volumes, compartment_id=compartment))
            for ad in ads:
                volumes.extend(
                    self.listing(self.block.list_boot_volumes, compartment_id=compartment, availability_domain=ad.name)
                )
        managed = [
            i
            for i in instances
            if state
            and i.freeform_tags.get("provision_id") == state["run_id"]
            and all(i.freeform_tags.get(k) == v for k, v in TAG.items())
        ]
        if len(managed) > 1:
            raise Blocked("Se encontró más de una instancia de esta provisión; revisar manualmente")
        if managed and (
            managed[0].compartment_id != self.compartment
            or managed[0].shape != SHAPE
            or managed[0].shape_config.ocpus != OCPUS
            or managed[0].shape_config.memory_in_gbs != MEMORY_GB
            or managed[0].lifecycle_state == "TERMINATED"
        ):
            raise Blocked("La instancia existente no coincide con la configuración prevista")
        existing = managed[0] if managed else None
        allocation = check_allocation(instances, volumes, existing.id if existing else None)
        images = self.listing(
            self.compute.list_images,
            self.compartment,
            operating_system="Canonical Ubuntu",
            operating_system_version="24.04",
            shape=SHAPE,
        )
        images = [
            i
            for i in images
            if i.compartment_id is None and "aarch64" in i.display_name and i.lifecycle_state == "AVAILABLE"
        ]
        if not images:
            raise Blocked("No hay una imagen oficial Ubuntu 24.04 ARM64 compatible")
        image = next((i for i in images if state and i.id == state.get("image_id")), None)
        if state and state.get("image_id") and image is None:
            raise Blocked("La imagen registrada ya no está disponible; revisar antes de cambiarla")
        image = image or max(images, key=lambda i: i.time_created)
        capacities = []
        for ad in ads:
            shape = next(
                (
                    s
                    for s in self.listing(self.compute.list_shapes, self.compartment, availability_domain=ad.name)
                    if s.shape == SHAPE
                ),
                None,
            )
            if shape is None or shape.billing_type not in ("LIMITED_FREE", "ALWAYS_FREE"):
                raise Blocked("A1 no está identificada como gratuita/elegible en la región")
            details = oci.core.models.CreateComputeCapacityReportDetails(
                compartment_id=self.compartment,
                availability_domain=ad.name,
                shape_availabilities=[
                    oci.core.models.CreateCapacityReportShapeAvailabilityDetails(
                        instance_shape=SHAPE,
                        instance_shape_config=oci.core.models.CapacityReportInstanceShapeConfig(
                            ocpus=OCPUS, memory_in_gbs=MEMORY_GB
                        ),
                    )
                ],
            )
            cap = self.call(self.compute.create_compute_capacity_report, details).data
            quotas = {}
            for name, required in (
                ("standard-a1-core-count", OCPUS),
                ("standard-a1-memory-count", MEMORY_GB),
                ("standard-a1-core-regional-count", OCPUS),
                ("standard-a1-memory-regional-count", MEMORY_GB),
            ):
                kwargs = {} if "regional" in name else {"availability_domain": ad.name}
                quota = self.call(
                    self.limits.get_resource_availability, "compute", name, self.compartment, **kwargs
                ).data
                quotas[name] = quota.available
                if quota.available is None or (not existing and quota.available < required):
                    raise Blocked(f"Cuota de servicio insuficiente: {name}; no implica más asignación gratuita")
            capacities.append(
                {
                    "ad": ad.name,
                    "service_quota_available": quotas,
                    "billing_type": shape.billing_type,
                    "status": cap.shape_availabilities[0].availability_status,
                }
            )
        return {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "region": home,
            **allocation,
            "image_id": image.id,
            "image_name": image.display_name,
            "capacity": capacities,
            "existing_instance_id": existing.id if existing else None,
        }

    def _step(self, state, state_path, name, create, get, listing, details):
        tags = {**TAG, "provision_id": state["run_id"]}
        details.freeform_tags = tags
        resource_id = state["ids"].get(name)
        if resource_id:
            item = self.call(get, resource_id).data
        else:
            matches = [
                r
                for r in self.listing(listing, self.compartment)
                if all(r.freeform_tags.get(k) == v for k, v in tags.items())
            ]
            if len(matches) > 1:
                raise Blocked(f"Más de un recurso para {name}; revisar manualmente")
            item = matches[0] if matches else None
        if item is None:
            token = state["tokens"].get(name)
            if token and time.time() - token["created_at"] >= 86400:
                raise Blocked(f"Token sin respuesta de {name} vencido; revisar OCI antes de volver a crear")
            if not token:
                token = {"value": uuid.uuid4().hex, "created_at": time.time()}
                state["tokens"][name] = token
                save_json(state_path, state)
            item = self.call(create, details, opc_retry_token=token["value"]).data
        if item.compartment_id != self.compartment or any(item.freeform_tags.get(k) != v for k, v in tags.items()):
            raise Blocked(f"El recurso {name} no pertenece a esta provisión")
        state["ids"][name] = item.id
        save_json(state_path, state)
        deadline = time.monotonic() + 600
        while item.lifecycle_state != "AVAILABLE":
            if item.lifecycle_state in ("TERMINATING", "TERMINATED") or time.monotonic() >= deadline:
                raise Blocked(f"El recurso {name} no está disponible; estado registrado para retomar")
            time.sleep(5)
            item = self.call(get, item.id).data
        # Los detalles que afectan conectividad/seguridad deben seguir coincidiendo.
        for field in ("vcn_id", "cidr_block", "security_list_ids", "route_table_id", "is_enabled"):
            wanted = getattr(details, field, None)
            if wanted is not None and getattr(item, field) != wanted:
                raise Blocked(f"La configuración de {name}.{field} cambió; no modificarla automáticamente")
        if getattr(details, "is_ipv6_enabled", None) is False and (
            getattr(item, "ipv6_cidr_blocks", None)
            or getattr(item, "ipv6_private_cidr_blocks", None)
            or getattr(item, "byoipv6_cidr_blocks", None)
        ):
            raise Blocked("La VCN contiene IPv6 no previsto")
        for field in ("ingress_security_rules", "egress_security_rules", "route_rules"):
            wanted = getattr(details, field, None)
            if wanted is not None and not rules_match(getattr(item, field), wanted):
                raise Blocked(f"Las reglas de {name} cambiaron; revisar antes de continuar")
        return item.id

    def apply(self, report, state, state_path, address, key, bootstrap):
        available = next((r for r in report["capacity"] if r["status"] == "AVAILABLE"), None)
        if not report["existing_instance_id"] and available is None:
            raise Blocked("OUT_OF_HOST_CAPACITY: sin capacidad; no se crearon red, VM ni discos")
        if state is None:
            state = {
                "schema": 1,
                "run_id": uuid.uuid4().hex,
                "ids": {},
                "tokens": {},
                "scope": self.scope(),
                "parameters": parameters(address, key, bootstrap),
                "image_id": report["image_id"],
            }
        if state["scope"] != self.scope() or state["parameters"] != parameters(address, key, bootstrap):
            raise Blocked("El diario pertenece a otra configuración; verificarlo contra OCI antes de retomarlo")
        save_json(state_path, state)
        model = oci.core.models
        common = {"compartment_id": self.compartment}
        vcn = self._step(
            state,
            state_path,
            "vcn",
            self.network.create_vcn,
            self.network.get_vcn,
            self.network.list_vcns,
            model.CreateVcnDetails(
                **common, display_name="nuevamente-vcn", cidr_block="10.38.0.0/16", is_ipv6_enabled=False
            ),
        )
        gateway = self._step(
            state,
            state_path,
            "gateway",
            self.network.create_internet_gateway,
            self.network.get_internet_gateway,
            self.network.list_internet_gateways,
            model.CreateInternetGatewayDetails(
                **common, vcn_id=vcn, display_name="nuevamente-internet", is_enabled=True
            ),
        )
        routes = self._step(
            state,
            state_path,
            "routes",
            self.network.create_route_table,
            self.network.get_route_table,
            self.network.list_route_tables,
            model.CreateRouteTableDetails(
                **common,
                vcn_id=vcn,
                display_name="nuevamente-routes",
                route_rules=[
                    model.RouteRule(
                        destination="0.0.0.0/0",
                        destination_type="CIDR_BLOCK",
                        network_entity_id=gateway,
                        route_type="STATIC",
                    )
                ],
            ),
        )
        security = self._step(
            state,
            state_path,
            "security",
            self.network.create_security_list,
            self.network.get_security_list,
            self.network.list_security_lists,
            model.CreateSecurityListDetails(
                **common,
                vcn_id=vcn,
                display_name="nuevamente-ingress",
                ingress_security_rules=ingress(address),
                egress_security_rules=[
                    model.EgressSecurityRule(
                        destination="0.0.0.0/0", destination_type="CIDR_BLOCK", protocol="all", is_stateless=False
                    )
                ],
            ),
        )
        subnet = self._step(
            state,
            state_path,
            "subnet",
            self.network.create_subnet,
            self.network.get_subnet,
            self.network.list_subnets,
            model.CreateSubnetDetails(
                **common,
                vcn_id=vcn,
                display_name="nuevamente-public",
                cidr_block="10.38.1.0/24",
                route_table_id=routes,
                security_list_ids=[security],
                prohibit_public_ip_on_vnic=False,
            ),
        )
        details = model.LaunchInstanceDetails(
            **common,
            availability_domain=available["ad"] if available else report["capacity"][0]["ad"],
            display_name="nuevamente-vm",
            shape=SHAPE,
            shape_config=model.LaunchInstanceShapeConfigDetails(ocpus=OCPUS, memory_in_gbs=MEMORY_GB),
            source_details=model.InstanceSourceViaImageDetails(
                image_id=state["image_id"], boot_volume_size_in_gbs=BOOT_GB, boot_volume_vpus_per_gb=10
            ),
            create_vnic_details=model.CreateVnicDetails(subnet_id=subnet, assign_public_ip=True, nsg_ids=[]),
            instance_options=model.InstanceOptions(are_legacy_imds_endpoints_disabled=True),
            agent_config=model.LaunchInstanceAgentConfigDetails(
                is_management_disabled=True, is_monitoring_disabled=False
            ),
            is_ai_enterprise_enabled=False,
            freeform_tags={**TAG, "provision_id": state["run_id"]},
            metadata={
                "ssh_authorized_keys": key,
                "user_data": base64.b64encode(cloud_init(address, bootstrap)).decode(),
            },
        )
        existing = report["existing_instance_id"]
        if existing:
            instance = self.call(self.compute.get_instance, existing).data
        else:
            fresh = self.preflight(state)
            if fresh["existing_instance_id"]:
                raise Blocked("Una instancia apareció durante la provisión; retomar para reutilizarla")
            if not any(
                r["ad"] == details.availability_domain and r["status"] == "AVAILABLE" for r in fresh["capacity"]
            ):
                raise Blocked("La capacidad dejó de estar disponible; red registrada, sin nuevo lanzamiento")
            token = state["tokens"].get("instance")
            if token and time.time() - token["created_at"] >= 86400:
                raise Blocked("Token de lanzamiento vencido: revisar la tenancy antes de reintentar")
            if not token:
                token = {"value": uuid.uuid4().hex, "created_at": time.time()}
                state["tokens"]["instance"] = token
                save_json(state_path, state)
            # Una sola intención de lanzamiento; el SDK no añade reintentos.
            instance = self.call(self.compute.launch_instance, details, opc_retry_token=token["value"]).data
        state["ids"]["instance"] = instance.id
        save_json(state_path, state)
        deadline = time.monotonic() + 600
        while instance.lifecycle_state != "RUNNING":
            if instance.lifecycle_state in ("TERMINATING", "TERMINATED") or time.monotonic() >= deadline:
                raise Blocked("La VM no llegó a RUNNING; revisar el diario y OCI antes de retomar")
            time.sleep(5)
            instance = self.call(self.compute.get_instance, instance.id).data
        if instance.image_id != state["image_id"]:
            raise Blocked("La VM existente usa otra imagen")
        attachments = self.listing(self.compute.list_vnic_attachments, self.compartment, instance_id=instance.id)
        primary = None
        for attachment in attachments:
            if attachment.lifecycle_state == "ATTACHED":
                vnic = self.call(self.network.get_vnic, attachment.vnic_id).data
                if vnic.is_primary:
                    primary = vnic
        if primary is None or primary.subnet_id != subnet or primary.nsg_ids:
            raise Blocked("La VNIC no coincide con la subred restringida o tiene NSG adicionales")
        state["public_ip"] = primary.public_ip
        save_json(state_path, state)
        return {
            "instance_id": instance.id,
            "public_ip": primary.public_ip,
            "status": instance.lifecycle_state,
            "bootstrap_verified": False,
            "external_ssh_verified": False,
            "console_cost_evidence": False,
        }

    def scope(self):
        return {"tenancy": self.config["tenancy"], "compartment": self.compartment, "region": self.config["region"]}


def parameters(address, key, bootstrap):
    return {
        "admin_cidr": address,
        "ssh_sha256": hashlib.sha256(key.encode()).hexdigest(),
        "bootstrap_sha256": hashlib.sha256(bootstrap.encode()).hexdigest(),
    }


def cloud_init(address, bootstrap):
    first, rest = bootstrap.split("\n", 1)
    return f'{first}\nexport NUEVAMENTE_ADMIN_CIDR="{admin_address(address)}"\n{rest}'.encode()


def ingress(address):
    m = oci.core.models
    rules = [
        m.IngressSecurityRule(
            source=source,
            source_type="CIDR_BLOCK",
            protocol="6",
            is_stateless=False,
            tcp_options=m.TcpOptions(destination_port_range=m.PortRange(min=port, max=port)),
        )
        for source, port in ((admin_address(address), 22), ("0.0.0.0/0", 80), ("0.0.0.0/0", 443))
    ]
    rules.append(
        m.IngressSecurityRule(
            source="0.0.0.0/0",
            source_type="CIDR_BLOCK",
            protocol="1",
            is_stateless=False,
            icmp_options=m.IcmpOptions(type=3, code=4),
        )
    )
    return rules


def rules_match(actual, expected):
    """Ignorar defaults del SDK, preservando cantidad y campos explícitos de las reglas."""

    def subset(a, e):
        if isinstance(e, dict):
            return isinstance(a, dict) and all(subset(a.get(k), v) for k, v in e.items() if v is not None)
        if isinstance(e, list):
            if not isinstance(a, list) or len(a) != len(e):
                return False
            remaining = list(a)
            for wanted in e:
                index = next((i for i, item in enumerate(remaining) if subset(item, wanted)), None)
                if index is None:
                    return False
                remaining.pop(index)
            return True
        return a == e

    return subset(oci.util.to_dict(actual), oci.util.to_dict(expected))


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise Blocked(
            "Existe un bloqueo de provisión: verificar que no haya otra ejecución antes de retirarlo"
        ) from None
    try:
        with handle:
            handle.write(datetime.now(timezone.utc).isoformat())
        yield
    finally:
        lock.unlink()


def local_state_path(value):
    path = Path(value).resolve()
    root = Path(__file__).resolve().parents[2] / ".data"
    if not path.is_relative_to(root.resolve()):
        raise Blocked("El diario y los reportes de esta herramienta deben quedar dentro de .data")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-file", required=True, help="Configuración OCI del operador, fuera del repositorio")
    parser.add_argument("--profile", default="DEFAULT")
    parser.add_argument("--compartment-id", required=True)
    parser.add_argument("--state-file", default=".data/oci47/state.json")
    parser.add_argument("--admin-cidr")
    parser.add_argument("--ssh-public-key", help="Solo la clave pública .pub; nunca la clave privada")
    parser.add_argument("--confirm-always-free", action="store_true")
    parser.add_argument(
        "--apply", action="store_true", help="Crear/reutilizar infraestructura validada; sin este flag solo consulta"
    )
    args = parser.parse_args()
    try:
        state_path = local_state_path(args.state_file)
        with locked(state_path):
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
            if state is not None and (
                not isinstance(state, dict)
                or state.get("schema") != 1
                or not all(k in state for k in ("run_id", "scope", "parameters", "ids", "tokens"))
            ):
                raise Blocked("El archivo no es un diario de provisión válido; no usar snapshots como estado vigente")
            bootstrap = Path(__file__).with_name("bootstrap.sh").read_text(encoding="utf-8")
            if args.apply:
                if not args.confirm_always_free or not args.admin_cidr or not args.ssh_public_key:
                    raise Blocked("--apply requiere confirmar Always Free, IPv4 administrativa /32 y clave pública")
                address, key = admin_address(args.admin_cidr), public_key(args.ssh_public_key)
            config = oci.config.from_file(args.config_file, args.profile)
            ctx = Infrastructure(config, args.compartment_id)
            if state and state["scope"] != ctx.scope():
                raise Blocked("El diario no corresponde a la tenancy/región/compartimento seleccionados")
            if state and args.apply and state["parameters"] != parameters(address, key, bootstrap):
                raise Blocked("Cambió la configuración del diario; revisar recursos existentes antes de continuar")
            report = ctx.preflight(state)
            report_path = state_path.parent / ("preflight-" + uuid.uuid4().hex + ".json")
            save_json(report_path, report)
            print(
                json.dumps(
                    {
                        "report": str(report_path),
                        "region": report["region"],
                        "capacity": report["capacity"],
                        "allocation_used": {
                            k: report[k] for k in ("a1_ocpus_used", "a1_memory_gb_used", "boot_block_gb_used")
                        },
                    }
                )
            )
            if args.apply:
                result = ctx.apply(report, state, state_path, address, key, bootstrap)
                print(json.dumps(result))
        return 0
    except Blocked as error:
        print(json.dumps({"blocked": str(error)}))
        return 2
    except oci.exceptions.ServiceError as error:
        print(json.dumps({"oci_error": error.code, "http_status": error.status}))
        return 3
    except (OSError, ValueError, oci.exceptions.ClientError):
        print(
            json.dumps(
                {
                    "error": "Revisar configuración OCI, clave pública, conectividad y archivos locales; no se muestran secretos"
                }
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
