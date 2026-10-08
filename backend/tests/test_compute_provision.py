"""Guardas de #47: los tests ordinarios no llaman a OCI ni crean recursos."""

import base64
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import Mock

import oci
import pytest

pytestmark = pytest.mark.unit
SPEC = importlib.util.spec_from_file_location(
    "compute_provision", Path(__file__).resolve().parents[2] / "infra/oci/provision_vm.py"
)
provision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provision)


def instance(state="RUNNING", cpu=2, ram=12, identity="vm"):
    return Obj(
        id=identity, lifecycle_state=state, shape=provision.SHAPE, shape_config=Obj(ocpus=cpu, memory_in_gbs=ram)
    )


def test_asignacion_cuenta_instancias_detenidas_y_volumenes_huerfanos():
    with pytest.raises(provision.Blocked):
        provision.check_allocation([instance("STOPPED")], [])
    with pytest.raises(provision.Blocked):
        provision.check_allocation([], [Obj(size_in_gbs=151, lifecycle_state="AVAILABLE")])
    result = provision.check_allocation([instance("TERMINATED")], [Obj(size_in_gbs=150, lifecycle_state="AVAILABLE")])
    assert result["boot_block_gb_used"] == 150


def test_retoma_vm_existente_sin_reservar_otras_dos_ocpu():
    assert (
        provision.check_allocation([instance()], [Obj(size_in_gbs=50, lifecycle_state="AVAILABLE")], "vm")[
            "a1_ocpus_used"
        ]
        == 2
    )


@pytest.mark.parametrize("address", ["0.0.0.0/0", "8.8.8.0/24", "127.0.0.1/32", "10.0.0.1/32", "::/0"])
def test_ssh_rechaza_fuentes_amplias_o_no_publicas(address):
    with pytest.raises(provision.Blocked):
        provision.admin_address(address)


def test_reglas_solo_permiten_ssh_admin_y_proxy():
    rules = provision.ingress("8.8.8.8/32")
    tcp = [r for r in rules if r.protocol == "6"]
    assert [
        (r.source, r.tcp_options.destination_port_range.min, r.tcp_options.destination_port_range.max) for r in tcp
    ] == [("8.8.8.8/32", 22, 22), ("0.0.0.0/0", 80, 80), ("0.0.0.0/0", 443, 443)]
    assert provision.rules_match(list(reversed(rules)), rules)
    broad = provision.ingress("8.8.8.8/32")
    broad[0].source = "0.0.0.0/0"
    assert not provision.rules_match(broad, rules)


def test_solo_clave_publica_ed25519(tmp_path):
    pub = tmp_path / "key.pub"
    raw = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"a" * 32
    value = "ssh-ed25519 " + base64.b64encode(raw).decode()
    pub.write_text(value + " comentario", encoding="utf-8")
    assert provision.public_key(pub) == value
    pub.write_text("-----BEGIN PRIVATE KEY-----", encoding="utf-8")
    with pytest.raises(provision.Blocked):
        provision.public_key(pub)


@pytest.fixture
def ctx():
    result = provision.Infrastructure.__new__(provision.Infrastructure)
    result.config = {"tenancy": "tenant", "region": "home"}
    result.compartment = "app"
    result.requests = 0
    result.network = Mock()
    result.compute = Mock()
    return result


def report(status="OUT_OF_HOST_CAPACITY"):
    return {"capacity": [{"ad": "AD-1", "status": status}], "existing_instance_id": None, "image_id": "ubuntu"}


def test_falta_de_capacidad_no_crea_recursos_ni_diario(ctx, tmp_path):
    path = tmp_path / "state.json"
    with pytest.raises(provision.Blocked, match="OUT_OF_HOST_CAPACITY"):
        ctx.apply(report(), None, path, "8.8.8.8/32", "public", "#!/bin/bash\n")
    assert not path.exists()
    assert not ctx.network.mock_calls
    assert not ctx.compute.mock_calls


def test_conflicto_de_contexto_no_modifica_infra(ctx, tmp_path):
    state = {"scope": {"tenancy": "otra"}, "parameters": {}}
    with pytest.raises(provision.Blocked, match="otra configuración"):
        ctx.apply(report("AVAILABLE"), state, tmp_path / "state.json", "8.8.8.8/32", "pub", "#!/bin/bash\n")
    assert not ctx.network.mock_calls


def test_reintentos_tecnicos_tres_totales_y_sin_sdk(ctx, monkeypatch):
    monkeypatch.setattr(provision.time, "sleep", lambda _: None)
    error = oci.exceptions.ServiceError(status=503, code="Unavailable", headers={}, message="temporal")
    request = Mock(side_effect=error)
    with pytest.raises(oci.exceptions.ServiceError):
        ctx.call(request, opc_retry_token="intencion")
    assert request.call_count == 3
    assert all(call.kwargs["opc_retry_token"] == "intencion" for call in request.call_args_list)
    assert all(
        isinstance(call.kwargs["retry_strategy"], oci.retry.NoneRetryStrategy) for call in request.call_args_list
    )


def test_out_of_host_capacity_no_se_reintenta_en_bucle(ctx):
    error = oci.exceptions.ServiceError(status=500, code="InternalError", headers={}, message="Out of host capacity")
    request = Mock(side_effect=error)
    with pytest.raises(oci.exceptions.ServiceError):
        ctx.call(request)
    assert request.call_count == 1


def test_listado_no_pierde_segunda_pagina(ctx):
    request = Mock(
        side_effect=[Obj(data=["primero"], headers={"opc-next-page": "cursor"}), Obj(data=["segundo"], headers={})]
    )
    assert ctx.listing(request) == ["primero", "segundo"]
    assert request.call_args_list[1].kwargs["page"] == "cursor"


def state(ctx):
    return {"scope": ctx.scope(), "run_id": "run", "ids": {}, "tokens": {}}


def test_ack_perdido_reutiliza_recurso_oci(ctx, tmp_path):
    journal = state(ctx)
    tags = {**provision.TAG, "provision_id": "run"}
    vcn = oci.core.models.Vcn(
        id="vcn", compartment_id="app", freeform_tags=tags, lifecycle_state="AVAILABLE", cidr_block="10.38.0.0/16"
    )
    listing = Mock(return_value=Obj(data=[vcn], headers={}))
    create = Mock()
    result = ctx._step(
        journal,
        tmp_path / "state.json",
        "vcn",
        create,
        Mock(),
        listing,
        oci.core.models.CreateVcnDetails(compartment_id="app", cidr_block="10.38.0.0/16", is_ipv6_enabled=False),
    )
    assert result == "vcn"
    create.assert_not_called()
    assert json.loads((tmp_path / "state.json").read_text())["ids"]["vcn"] == "vcn"


def test_token_vencido_sin_ack_exige_revision(ctx, tmp_path):
    journal = state(ctx)
    journal["tokens"]["vcn"] = {"value": "expired", "created_at": time.time() - 90000}
    create = Mock()
    with pytest.raises(provision.Blocked, match="vencido"):
        ctx._step(
            journal,
            tmp_path / "state.json",
            "vcn",
            create,
            Mock(),
            Mock(return_value=Obj(data=[], headers={})),
            oci.core.models.CreateVcnDetails(compartment_id="app", cidr_block="10.38.0.0/16"),
        )
    create.assert_not_called()


def test_bloqueo_local_impide_dos_ejecuciones(tmp_path):
    path = tmp_path / "state.json"
    with provision.locked(path):
        with pytest.raises(provision.Blocked):
            with provision.locked(path):
                pytest.fail("Se adquirió un bloqueo ya existente")
    assert not path.with_suffix(".lock").exists()


def test_diario_fuera_de_data_es_rechazado():
    outside = Path(provision.__file__).resolve().parents[2] / "archivo.json"
    with pytest.raises(provision.Blocked):
        provision.local_state_path(outside)


def test_cloud_init_incluye_solo_cidr_validado():
    data = provision.cloud_init("8.8.8.8/32", "#!/usr/bin/env bash\necho listo\n").decode()
    assert data.startswith('#!/usr/bin/env bash\nexport NUEVAMENTE_ADMIN_CIDR="8.8.8.8/32"\n')
    with pytest.raises(ValueError):
        provision.cloud_init('8.8.8.8/32"; touch /tmp/x', "#!/bin/bash\n")


@pytest.mark.parametrize("snapshot", [[], {"image_id": "antiguo"}])
def test_snapshot_no_se_usa_como_diario(snapshot, tmp_path, monkeypatch):
    path = tmp_path / ".data/state.json"
    path.parent.mkdir()
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setattr(provision, "__file__", str(tmp_path / "infra/oci/provision_vm.py"))
    config = Mock()
    monkeypatch.setattr(provision.oci.config, "from_file", config)
    monkeypatch.setattr(
        sys, "argv", ["provision", "--config-file", "unused", "--compartment-id", "app", "--state-file", str(path)]
    )
    assert provision.main() == 2
    config.assert_not_called()


def test_receta_crea_solo_a1_y_subred_con_lista_restringida(ctx, tmp_path):
    model = oci.core.models
    path = tmp_path / "state.json"
    ctx.preflight = Mock(return_value=report("AVAILABLE"))
    resources = {
        "vcn": model.Vcn,
        "gateway": model.InternetGateway,
        "routes": model.RouteTable,
        "security": model.SecurityList,
        "subnet": model.Subnet,
    }
    for name, cls in resources.items():

        def create(details, _name=name, _cls=cls, **kwargs):
            assert kwargs["opc_retry_token"] == json.loads(path.read_text())["tokens"][_name]["value"]
            fields = {k: getattr(details, k) for k in _cls().swagger_types if hasattr(details, k)}
            fields.update(id=_name, lifecycle_state="AVAILABLE")
            return Obj(data=_cls(**fields), headers={})

        method_name = {
            "vcn": "vcn",
            "gateway": "internet_gateway",
            "routes": "route_table",
            "security": "security_list",
            "subnet": "subnet",
        }[name]
        getattr(ctx.network, "create_" + method_name).side_effect = create
        plural = {
            "vcn": "vcns",
            "gateway": "internet_gateways",
            "routes": "route_tables",
            "security": "security_lists",
            "subnet": "subnets",
        }[name]
        getattr(ctx.network, "list_" + plural).return_value = Obj(data=[], headers={})

    def launch(details, **kwargs):
        assert kwargs["opc_retry_token"] == json.loads(path.read_text())["tokens"]["instance"]["value"]
        assert details.shape == provision.SHAPE
        assert details.shape_config.ocpus == 2 and details.shape_config.memory_in_gbs == 12
        assert details.source_details.boot_volume_size_in_gbs == 50
        assert details.source_details.boot_volume_vpus_per_gb == 10
        assert details.source_details.image_id == "ubuntu"
        assert details.create_vnic_details.nsg_ids == []
        assert not details.is_ai_enterprise_enabled
        assert details.instance_options.are_legacy_imds_endpoints_disabled
        return Obj(data=model.Instance(id="vm", image_id="ubuntu", lifecycle_state="RUNNING"), headers={})

    ctx.compute.launch_instance.side_effect = launch
    ctx.compute.list_vnic_attachments.return_value = Obj(
        data=[Obj(lifecycle_state="ATTACHED", vnic_id="vnic")], headers={}
    )
    ctx.network.get_vnic.return_value = Obj(
        data=Obj(is_primary=True, subnet_id="subnet", nsg_ids=[], public_ip="ip_de_prueba")
    )
    result = ctx.apply(report("AVAILABLE"), None, path, "8.8.8.8/32", "public", "#!/bin/bash\necho inicio\n")
    details = ctx.network.create_subnet.call_args.args[0]
    assert details.security_list_ids == ["security"]
    assert result["bootstrap_verified"] is False
    assert result["external_ssh_verified"] is False
    assert result["console_cost_evidence"] is False
    assert ctx.compute.launch_instance.call_count == 1
