"""OCI real síncrono: sin fallback, bucket preexistente y presupuesto por solicitud."""

import base64
import hashlib
import inspect
import random
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock

import oci

from app.config import Configuracion
from app.storage.object_keys import BUCKET_PRODUCCION, validar_nombre
from app.storage.oci_budget import PresupuestoOCI
from app.storage.provider import (
    ListedObject,
    StorageBudgetExceeded,
    StorageConfigError,
    StorageConflict,
    StorageNotFound,
    StoragePage,
    StorageProvider,
    StorageUnavailable,
    StoredObject,
)

SIN_REINTENTOS = oci.retry.NoneRetryStrategy()


class _TransitorioOCI(StorageUnavailable):
    def __init__(self, retry_after=0.0):
        self.retry_after = retry_after
        super().__init__("OCI no está disponible temporalmente; sin fallback")


def _headers(respuesta):
    return {k.lower(): v for k, v in respuesta.headers.items()}


def _transitorio(error):
    if isinstance(error, oci.exceptions.ServiceError):
        return error.status in (408, 429, 500, 502, 503, 504)
    return isinstance(error, (oci.exceptions.RequestException, OSError, TimeoutError))


def _retry_after(error):
    if isinstance(error, _TransitorioOCI):
        return error.retry_after
    headers = getattr(error, "headers", {}) or {}
    valor = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    if valor is None:
        return 0.0
    try:
        segundos = float(valor)
        return max(0.0, segundos) if 0 <= segundos < float("inf") else 0.0
    except (ValueError, TypeError):
        try:
            fecha = parsedate_to_datetime(valor)
            if fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=timezone.utc)
            return max(0.0, (fecha - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0.0


class OCIObjectStorageProvider(StorageProvider):
    def __init__(
        self,
        configuracion: Configuracion,
        *,
        cliente=None,
        identidad=None,
        tenancy_id: str | None = None,
        presupuesto: PresupuestoOCI | None = None,
        dormir=time.sleep,
        crear_bucket=False,
    ):
        self.cfg, self._dormir = configuracion, dormir
        self.solicitudes = 0
        self._contador_lock = Lock()
        self._clientes_propios = cliente is None
        self.cliente = self.identidad = None
        if configuracion.mock_oci:
            raise StorageConfigError("El proveedor real requiere MOCK_OCI=0")
        if configuracion.oci_bucket_name != BUCKET_PRODUCCION:
            raise StorageConfigError("OCI_BUCKET_NAME debe ser nuevamente-contenidos-educativos")
        if configuracion.oci_compartment_id in ("", "placeholder") or configuracion.oci_region in (
            "",
            "placeholder",
            "home-region-de-la-tenancy",
        ):
            raise StorageConfigError(
                "Configurar OCI_BUCKET_NAME, OCI_COMPARTMENT_ID, OCI_REGION y OCI_CONFIG_FILE; MOCK_OCI=1 es solo desarrollo"
            )
        if not configuracion.oci_always_free_confirmed:
            raise StorageConfigError(
                "Confirmar capacidad Always Free disponible con OCI_ALWAYS_FREE_CONFIRMED=1; no se depende del trial"
            )
        if (cliente is None) != (identidad is None):
            raise StorageConfigError("Inyectar ambos clientes SDK para las pruebas")
        try:
            if cliente is None:
                cliente, identidad, tenancy_id = self._clientes()
            self.cliente, self.identidad = cliente, identidad
            for servicio, metodos in (
                (cliente, ("get_namespace", "get_bucket", "put_object", "get_object", "list_objects", "delete_object")),
                (identidad, ("list_region_subscriptions",)),
            ):
                if any(
                    not callable(getattr(servicio, nombre, None))
                    or inspect.iscoroutinefunction(getattr(servicio, nombre))
                    for nombre in metodos
                ):
                    raise StorageConfigError("OCI requiere clientes SDK síncronos")
            if not tenancy_id:
                raise StorageConfigError("La autenticación OCI no incluye tenancy")
            self.presupuesto = presupuesto or PresupuestoOCI(
                Path(configuracion.data_dir) / "oci_budget.sqlite3",
                solicitudes=configuracion.oci_max_requests_per_month,
                bytes_maximos=configuracion.oci_max_storage_bytes,
            )
            regiones = self._llamar(identidad.list_region_subscriptions, tenancy_id, con_request_id=False)
            principales = [r.region_name for r in regiones.data if r.is_home_region is True]
            if principales != [configuracion.oci_region]:
                raise StorageConfigError("OCI_REGION no coincide con la home region verificada de la tenancy")
            self.namespace = self._llamar(cliente.get_namespace, compartment_id=configuracion.oci_compartment_id).data
            if not isinstance(self.namespace, str) or not self.namespace:
                raise StorageUnavailable("OCI no devolvió un namespace válido")
            try:
                bucket = self._llamar(cliente.get_bucket, self.namespace, BUCKET_PRODUCCION).data
            except StorageNotFound:
                if not crear_bucket:
                    raise
                detalles = oci.object_storage.models.CreateBucketDetails(
                    name=BUCKET_PRODUCCION,
                    compartment_id=configuracion.oci_compartment_id,
                    storage_tier="Standard",
                    public_access_type="NoPublicAccess",
                    versioning="Disabled",
                    auto_tiering="Disabled",
                    object_events_enabled=False,
                )
                self._llamar(cliente.create_bucket, self.namespace, detalles)
                bucket = self._llamar(cliente.get_bucket, self.namespace, BUCKET_PRODUCCION).data
            self._validar_bucket(bucket)
            # Inventario remoto para no tratar un bucket ya utilizado como vacío.
            cursor = None
            vistos = set()
            while True:
                pagina = self._llamar(
                    cliente.list_objects,
                    self.namespace,
                    BUCKET_PRODUCCION,
                    fields="name,size",
                    limit=1000,
                    **({"start": cursor} if cursor else {}),
                ).data
                self.presupuesto.incorporar_inventario((o.name, o.size) for o in pagina.objects)
                cursor = pagina.next_start_with
                if not cursor:
                    break
                if cursor in vistos:
                    raise StorageUnavailable("OCI devolvió un cursor de inventario repetido")
                vistos.add(cursor)
        except StorageNotFound:
            self.cerrar()
            raise StorageConfigError(
                "El bucket no está disponible: revisar región/compartimento y permisos; si falta, crearlo manualmente"
            ) from None
        except Exception:
            self.cerrar()
            raise

    def _clientes(self):
        try:
            if self.cfg.oci_auth_type == "api_key":
                ajustes = oci.config.from_file(self.cfg.oci_config_file, self.cfg.oci_config_profile)
                oci.config.validate_config(ajustes)
                if ajustes["region"] != self.cfg.oci_region:
                    raise StorageConfigError("La región de OCI_CONFIG_FILE no coincide con OCI_REGION")
                signer = None
                tenancy = ajustes["tenancy"]
            else:
                signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner(
                    retry_strategy=SIN_REINTENTOS, federation_client_retry_strategy=SIN_REINTENTOS
                )
                if signer.region != self.cfg.oci_region:
                    raise StorageConfigError("La región de la identidad de instancia no coincide con OCI_REGION")
                ajustes, tenancy = {"region": self.cfg.oci_region}, signer.tenancy_id
            opciones = dict(
                retry_strategy=SIN_REINTENTOS,
                timeout=(min(10, self.cfg.oci_timeout_seconds), self.cfg.oci_timeout_seconds),
                circuit_breaker_strategy=None,
            )
            if signer is not None:
                opciones["signer"] = signer
            cliente = oci.object_storage.ObjectStorageClient(ajustes, **opciones)
            try:
                identidad = oci.identity.IdentityClient(ajustes, **opciones)
            except Exception:
                cliente.base_client.session.close()
                raise
            return cliente, identidad, tenancy
        except StorageConfigError:
            raise
        except Exception:
            raise StorageConfigError(
                "Credenciales OCI inválidas: revisar OCI_CONFIG_FILE/OCI_CONFIG_PROFILE o OCI_AUTH_TYPE=instance_principal y sus permisos"
            ) from None

    def _validar_bucket(self, bucket):
        if bucket.compartment_id != self.cfg.oci_compartment_id:
            raise StorageConfigError("El bucket no pertenece al compartimento del proyecto")
        if (
            bucket.storage_tier != "Standard"
            or bucket.public_access_type != "NoPublicAccess"
            or bucket.versioning != "Disabled"
            or bucket.auto_tiering not in (None, "Disabled")
            or bucket.replication_enabled is not False
            or bucket.kms_key_id is not None
        ):
            raise StorageConfigError(
                "El bucket debe ser privado, Standard, con clave Oracle, sin versionado, replicación ni auto-tiering"
            )

    def _esperar(self, error, intento, deadline):
        pausa = max(0.5 * 2**intento * random.uniform(0.75, 1.25), _retry_after(error))
        fin = time.monotonic() + pausa
        if fin >= deadline:
            raise StorageUnavailable("Retry-After de OCI excede el tiempo disponible")
        while (restante := fin - time.monotonic()) > 0:
            self._dormir(restante)

    def _llamar(self, funcion, *args, reintentar=True, con_request_id=True, _deadline=None, **kwargs):
        request_id = uuid.uuid4().hex
        deadline = _deadline if _deadline is not None else time.monotonic() + 300
        for intento in range(3 if reintentar else 1):
            if time.monotonic() >= deadline:
                raise StorageUnavailable("La operación OCI agotó su deadline")
            self.presupuesto.reservar_solicitud()
            with self._contador_lock:
                self.solicitudes += 1
            try:
                opciones = dict(retry_strategy=SIN_REINTENTOS, **kwargs)
                if con_request_id:
                    opciones.setdefault("opc_client_request_id", request_id)
                respuesta = funcion(*args, **opciones)
                if time.monotonic() >= deadline:
                    raise _TransitorioOCI()
                return respuesta
            except Exception as error:
                if _transitorio(error):
                    if not reintentar or intento == 2:
                        raise _TransitorioOCI(_retry_after(error)) from None
                    self._esperar(error, intento, deadline)
                    continue
                if isinstance(error, oci.exceptions.ServiceError):
                    if error.status == 404:
                        raise StorageNotFound("El objeto OCI no existe o no está disponible") from None
                    if error.status == 412:
                        raise StorageConflict("La condición ETag del objeto OCI no se cumple") from None
                    raise StorageUnavailable("OCI rechazó la operación; revisar permisos/configuración") from None
                if isinstance(error, StorageUnavailable):
                    raise
                raise StorageUnavailable("El SDK OCI no pudo completar la operación") from None
        raise StorageUnavailable("OCI no completó la operación")

    def _leer(self, object_name, *, if_match=None, reintentar=True, _deadline=None):
        def leer(*args, **kwargs):
            respuesta = self.cliente.get_object(*args, **kwargs)
            try:
                headers = _headers(respuesta)
                if not headers.get("etag"):
                    raise StorageUnavailable("OCI no devolvió un ETag válido")
                bloques, tamanio = [], 0
                for bloque in respuesta.data.iter_content(chunk_size=65536):
                    if not isinstance(bloque, bytes):
                        raise StorageUnavailable("OCI devolvió contenido inválido")
                    tamanio += len(bloque)
                    if tamanio > self.presupuesto.bytes_maximos:
                        raise StorageUnavailable("El objeto OCI excede el presupuesto de capacidad")
                    bloques.append(bloque)
                return b"".join(bloques), headers
            finally:
                respuesta.data.close()

        return self._llamar(
            leer,
            self.namespace,
            BUCKET_PRODUCCION,
            object_name,
            reintentar=reintentar,
            _deadline=_deadline,
            **({"if_match": if_match} if if_match is not None else {}),
        )

    def _confirmar_upload(self, token, object_name, cuerpo, headers):
        self.presupuesto.confirmar_upload(token, object_name, len(cuerpo))
        return StoredObject(object_name, headers["etag"], f"oci://{BUCKET_PRODUCCION}/{object_name}", "oci")

    def _reconciliar(self, token, object_name, cuerpo, deadline):
        try:
            leido, headers = self._leer(object_name, reintentar=False, _deadline=deadline)
        except StorageBudgetExceeded:
            raise
        except (StorageNotFound, _TransitorioOCI):
            return None
        if leido == cuerpo and headers.get("opc-meta-nm-operation") == token:
            return self._confirmar_upload(token, object_name, cuerpo, headers)
        return None

    def upload(self, object_name, content, *, content_type="application/octet-stream", crear_solo=False, if_match=None):
        validar_nombre(object_name)
        if crear_solo and if_match is not None:
            raise ValueError("crear_solo e if_match son mutuamente excluyentes")
        if (
            not isinstance(content, (bytes, str))
            or not isinstance(content_type, str)
            or not content_type
            or any(c in content_type for c in "\r\n")
        ):
            raise ValueError("Contenido/MIME inválidos")
        cuerpo = content.encode("utf-8") if isinstance(content, str) else content
        token, huella = uuid.uuid4().hex, hashlib.sha256(cuerpo).hexdigest()
        self.presupuesto.reservar_upload(token, object_name, len(cuerpo))
        incierta = False
        deadline = time.monotonic() + 300
        opciones = dict(
            opc_client_request_id=token,
            content_type=content_type,
            content_md5=base64.b64encode(hashlib.md5(cuerpo, usedforsecurity=False).digest()).decode(),
            opc_meta={"nm-operation": token, "nm-sha256": huella},
        )
        if crear_solo:
            opciones["if_none_match"] = "*"
        if if_match is not None:
            opciones["if_match"] = if_match
        try:
            for intento in range(3):
                try:
                    resultado = self._llamar(
                        self.cliente.put_object,
                        self.namespace,
                        BUCKET_PRODUCCION,
                        object_name,
                        cuerpo,
                        reintentar=False,
                        _deadline=deadline,
                        **opciones,
                    )
                except StorageConflict:
                    if incierta:
                        confirmado = self._reconciliar(token, object_name, cuerpo, deadline)
                        if confirmado is not None:
                            return confirmado
                    # OCI devuelve 412 también para If-Match sobre un objeto ausente.
                    if if_match is not None:
                        self._leer(object_name, reintentar=False, _deadline=deadline)
                    raise
                except _TransitorioOCI as error:
                    incierta = True
                    confirmado = self._reconciliar(token, object_name, cuerpo, deadline)
                    if confirmado is not None:
                        return confirmado
                    if intento == 2:
                        raise
                    self._esperar(error, intento, deadline)
                    continue
                incierta = True
                etag = _headers(resultado).get("etag")
                if not etag:
                    raise StorageUnavailable("OCI no confirmó el ETag de la escritura")
                leido, headers = self._leer(object_name, if_match=etag, _deadline=deadline)
                if leido != cuerpo or headers.get("opc-meta-nm-operation") != token:
                    raise StorageUnavailable("OCI no confirmó el contenido escrito")
                return self._confirmar_upload(token, object_name, cuerpo, headers)
        finally:
            if not incierta:
                self.presupuesto.liberar_reserva(token)
        raise StorageUnavailable("No se pudo confirmar la escritura OCI")

    def get(self, object_name, *, if_match=None):
        validar_nombre(object_name)
        return self._leer(object_name, if_match=if_match)[0]

    def get_as_text(self, object_name, *, if_match=None):
        return self.get(object_name, if_match=if_match).decode("utf-8")

    def list(self, prefix="", *, limit=100, cursor=None):
        if prefix:
            validar_nombre(prefix.rstrip("/"))
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit debe estar entre 1 y 1000")
        if cursor is not None:
            validar_nombre(cursor)
        opciones = dict(prefix=prefix, limit=limit, fields="name,size,etag,timeModified")
        if cursor is not None:
            opciones["start_after"] = cursor
        pagina = self._llamar(self.cliente.list_objects, self.namespace, BUCKET_PRODUCCION, **opciones).data
        if any(
            not isinstance(o.etag, str) or not o.etag or type(o.size) is not int or o.size < 0 for o in pagina.objects
        ):
            raise StorageUnavailable("OCI no devolvió metadata completa de los objetos")
        items = [ListedObject(o.name, o.etag, o.size, o.time_modified) for o in pagina.objects]
        nombres = [i.object_name for i in items]
        if (
            len(items) > limit
            or nombres != sorted(set(nombres))
            or any(not n.startswith(prefix) or (cursor is not None and n <= cursor) for n in nombres)
        ):
            raise StorageUnavailable("OCI devolvió una página inconsistente")
        for nombre in nombres:
            validar_nombre(nombre)
        if pagina.next_start_with and not items:
            raise StorageUnavailable("OCI devolvió un cursor sin objetos")
        return StoragePage(items, nombres[-1] if pagina.next_start_with else None)

    def delete(self, object_name):
        validar_nombre(object_name)
        incierta = False
        deadline = time.monotonic() + 300
        for intento in range(3):
            try:
                self._llamar(
                    self.cliente.delete_object,
                    self.namespace,
                    BUCKET_PRODUCCION,
                    object_name,
                    reintentar=False,
                    _deadline=deadline,
                )
                self.presupuesto.confirmar_borrado(object_name)
                return
            except StorageNotFound:
                if incierta:
                    self.presupuesto.confirmar_borrado(object_name)
                    return
                raise
            except _TransitorioOCI as error:
                incierta = True
                if intento == 2:
                    raise
                self._esperar(error, intento, deadline)

    def cerrar(self):
        if self._clientes_propios:
            for cliente in (self.cliente, self.identidad):
                if cliente is not None:
                    cliente.base_client.session.close()
