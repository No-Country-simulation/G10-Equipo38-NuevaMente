"""#14: contrato OCI, escrituras inciertas, SDK sin retries y límites persistentes."""

import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock

import oci
import pytest
from app.config import Configuracion
from app.storage.object_keys import (
    BUCKET_PRODUCCION,
    clave_demo,
    clave_documento,
    clave_export,
    clave_original,
    clave_output,
    clave_progress,
    clave_workspace,
)
from app.storage.oci_budget import PresupuestoOCI
from app.storage.oci_real import OCIObjectStorageProvider
from app.storage.oci_storage import LocalMockStorageProvider, get_storage_provider
from app.storage.provider import (
    StorageBudgetExceeded,
    StorageConfigError,
    StorageConflict,
    StorageInvalidName,
    StorageNotFound,
    StorageUnavailable,
)

pytestmark = pytest.mark.unit


def error(status, retry_after=None):
    return oci.exceptions.ServiceError(
        status, "Error", {"Retry-After": str(retry_after)} if retry_after else {}, "SECRETO-DEL-PROVEEDOR"
    )


class Flujo:
    def __init__(self, contenido):
        self.contenido, self.cerrado = contenido, False

    def iter_content(self, chunk_size):
        yield self.contenido

    def close(self):
        self.cerrado = True


class SDK:
    def __init__(self):
        self.calls, self.objetos, self.errores = [], {}, defaultdict(deque)
        self.streams = []
        self.region = "us-ashburn-1"
        self.bucket = NS(
            compartment_id="ocid1.compartment.oc1..nm",
            storage_tier="Standard",
            public_access_type="NoPublicAccess",
            versioning="Disabled",
            auto_tiering="Disabled",
            replication_enabled=False,
            kms_key_id=None,
        )
        self.perder_ack = False

    def _call(self, nombre, args, kw):
        assert isinstance(kw["retry_strategy"], oci.retry.NoneRetryStrategy)
        if nombre == "regiones":
            assert "opc_client_request_id" not in kw
        self.calls.append((nombre, args, kw))
        if self.errores[nombre]:
            fallo = self.errores[nombre].popleft()
            if fallo is not None:
                raise fallo

    def list_region_subscriptions(self, tenancy, **kw):
        self._call("regiones", (tenancy,), kw)
        return NS(data=[NS(region_name=self.region, is_home_region=True)])

    def get_namespace(self, **kw):
        self._call("namespace", (), kw)
        return NS(data="namespace-test")

    def get_bucket(self, ns, bucket, **kw):
        self._call("bucket", (ns, bucket), kw)
        if self.bucket is None:
            raise error(404)
        return NS(data=self.bucket)

    def create_bucket(self, ns, detalles, **kw):
        self._call("crear_bucket", (ns, detalles), kw)
        self.bucket = NS(
            compartment_id=detalles.compartment_id,
            storage_tier=detalles.storage_tier,
            public_access_type=detalles.public_access_type,
            versioning=detalles.versioning,
            auto_tiering=detalles.auto_tiering,
            replication_enabled=False,
            kms_key_id=None,
        )
        return NS(data=self.bucket)

    def put_object(self, ns, bucket, nombre, cuerpo, **kw):
        self._call("put", (ns, bucket, nombre, cuerpo), kw)
        anterior = self.objetos.get(nombre)
        if kw.get("if_none_match") == "*" and anterior is not None:
            raise error(412)
        if "if_match" in kw and (anterior is None or kw["if_match"] != anterior["etag"]):
            raise error(412)
        assert kw["content_md5"]
        etag = str(len(self.calls))
        headers = {"etag": etag, **{"opc-meta-" + k: v for k, v in kw["opc_meta"].items()}}
        self.objetos[nombre] = dict(contenido=cuerpo, etag=etag, headers=headers)
        if self.perder_ack:
            self.perder_ack = False
            raise oci.exceptions.RequestException("RESPUESTA-PERDIDA")
        return NS(headers={"etag": etag})

    def get_object(self, ns, bucket, nombre, **kw):
        self._call("get", (ns, bucket, nombre), kw)
        objeto = self.objetos.get(nombre)
        if objeto is None:
            raise error(404)
        if "if_match" in kw and kw["if_match"] != objeto["etag"]:
            raise error(412)
        stream = Flujo(objeto["contenido"])
        self.streams.append(stream)
        return NS(data=stream, headers=objeto["headers"])

    def list_objects(self, ns, bucket, **kw):
        self._call("list", (ns, bucket), kw)
        nombres = sorted(
            n
            for n in self.objetos
            if n.startswith(kw.get("prefix", "")) and n > kw.get("start_after", "") and n >= kw.get("start", "")
        )
        seleccionados = nombres[: kw["limit"]]
        objetos = [
            NS(
                name=n,
                etag=self.objetos[n]["etag"],
                size=len(self.objetos[n]["contenido"]),
                time_modified=datetime.now(timezone.utc),
            )
            for n in seleccionados
        ]
        return NS(
            data=NS(objects=objetos, next_start_with=nombres[kw["limit"]] if len(nombres) > kw["limit"] else None)
        )

    def delete_object(self, ns, bucket, nombre, **kw):
        self._call("delete", (ns, bucket, nombre), kw)
        if nombre not in self.objetos:
            raise error(404)
        del self.objetos[nombre]
        return NS(headers={})


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    reloj = [time.monotonic()]
    monkeypatch.setattr("app.storage.oci_real.time.monotonic", lambda: reloj[0])

    def dormir(segundos):
        reloj[0] += segundos

    cfg = Configuracion(
        _env_file=None,
        mock_oci=False,
        mock_gemini=True,
        oci_always_free_confirmed=True,
        oci_region="us-ashburn-1",
        oci_compartment_id="ocid1.compartment.oc1..nm",
        data_dir=str(tmp_path),
    )
    sdk = SDK()

    def crear(**kw):
        return OCIObjectStorageProvider(
            cfg, cliente=sdk, identidad=sdk, tenancy_id="ocid1.tenancy.oc1..nm", dormir=dormir, **kw
        )

    return cfg, sdk, crear, reloj


def test_contrato_completo_y_factory_sin_fallback(entorno, monkeypatch):
    cfg, sdk, crear, _ = entorno
    real = crear()
    assert real.presupuesto.resumen()["solicitudes"] == len(sdk.calls) == 4
    nombre = clave_workspace("ws_1")
    objeto = real.upload(nombre, "á", content_type="application/json", crear_solo=True)
    assert objeto.proveedor == "oci" and objeto.uri == f"oci://{BUCKET_PRODUCCION}/{nombre}"
    assert real.get(nombre, if_match=objeto.etag) == "á".encode()
    assert real.get_as_text(nombre) == "á"
    with pytest.raises(StorageConflict):
        real.upload(nombre, "á", crear_solo=True)
    with pytest.raises(StorageConflict):
        real.upload(nombre, "otra", if_match="antiguo")
    nuevo = real.upload(nombre, "nueva", if_match=objeto.etag)
    with pytest.raises(StorageConflict):
        real.get(nombre, if_match=objeto.etag)
    assert real.list("workspaces/").items[0].etag == nuevo.etag
    real.delete(nombre)
    with pytest.raises(StorageNotFound):
        real.get(nombre)
    with pytest.raises(StorageNotFound):
        real.delete(nombre)
    with pytest.raises(StorageNotFound):
        real.upload(nombre, "otra", if_match=nuevo.etag)
    assert all(s.cerrado for s in sdk.streams)
    assert real.presupuesto.resumen()["solicitudes"] == len(sdk.calls)
    fabrica = Mock(return_value=real)
    monkeypatch.setattr("app.storage.oci_storage.OCIObjectStorageProvider", fabrica)
    assert get_storage_provider(configuracion=cfg) is real
    fabrica.assert_called_once_with(cfg)


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("compartment_id", "otro"),
        ("storage_tier", "Archive"),
        ("public_access_type", "ObjectRead"),
        ("versioning", "Enabled"),
        ("auto_tiering", "InfrequentAccess"),
        ("replication_enabled", True),
        ("kms_key_id", "clave"),
    ],
)
def test_bucket_inseguro_o_fuera_de_asignacion_se_rechaza(entorno, campo, valor):
    _, sdk, crear, _ = entorno
    setattr(sdk.bucket, campo, valor)
    with pytest.raises(StorageConfigError):
        crear()
    assert not any(c[0] in ("put", "crear_bucket") for c in sdk.calls)


def test_region_distinta_rechaza_antes_de_object_storage(entorno):
    _, sdk, crear, _ = entorno
    sdk.region = "sa-saopaulo-1"
    with pytest.raises(StorageConfigError, match="home region"):
        crear()
    assert [c[0] for c in sdk.calls] == ["regiones"]


def test_bucket_no_se_crea_silenciosamente(entorno):
    _, sdk, crear, _ = entorno
    sdk.bucket = None
    with pytest.raises(StorageConfigError, match="manualmente"):
        crear()
    assert not any(c[0] == "crear_bucket" for c in sdk.calls)
    proveedor = crear(crear_bucket=True)
    assert proveedor.namespace == "namespace-test"
    assert len([c for c in sdk.calls if c[0] == "crear_bucket"]) == 1


@pytest.mark.parametrize("valor", ["..", "a/b", "archivo con espacios", "", "ñ"])
def test_claves_se_generan_con_ids_no_con_nombre_original(valor):
    with pytest.raises(StorageInvalidName):
        clave_original("ws_1", valor)


def test_prefijos_exactos():
    assert clave_workspace("w") == "workspaces/w/manifest.json"
    assert clave_original("w", "d") == "source_documents/w/d/original"
    assert clave_documento("w", "d") == "source_documents/w/d/manifest.json"
    assert clave_output("w", "g") == "outputs/w/g/content.json"
    assert clave_export("w", "g", "apkg") == "exports/w/g/apkg"
    assert clave_export("w", "g", "md") == "exports/w/g/md"
    assert clave_progress("w") == "progress/w/state.json"
    assert clave_demo("vcn/original.pdf") == "demo/vcn/original.pdf"


@pytest.mark.parametrize("formato", ["markdown", "html"])
def test_exportaciones_respetan_formatos_del_contrato(formato):
    with pytest.raises(StorageInvalidName):
        clave_export("w", "g", formato)


@pytest.mark.parametrize("status", [400, 401, 403])
def test_error_permanente_no_retry_ni_fallback(entorno, status):
    _, sdk, crear, _ = entorno
    proveedor = crear()
    sdk.errores["put"].append(error(status))
    with pytest.raises(StorageUnavailable) as info:
        proveedor.upload("demo/prueba", "x")
    assert "SECRETO" not in str(info.value)
    assert len([c for c in sdk.calls if c[0] == "put"]) == 1
    assert proveedor.presupuesto.resumen()["reservas_inciertas"] == 0


@pytest.mark.parametrize("fallo", [error(503), oci.exceptions.RequestException("DNS roto"), TimeoutError("timeout")])
def test_retry_de_lectura_tres_intentos_contabilizados(entorno, fallo):
    _, sdk, crear, _ = entorno
    proveedor = crear()
    sdk.errores["get"].extend([fallo] * 3)
    with pytest.raises(StorageUnavailable):
        proveedor.get("demo/prueba")
    assert [c[0] for c in sdk.calls].count("get") == 3
    assert proveedor.presupuesto.resumen()["solicitudes"] == len(sdk.calls)


def test_retry_after_se_conserva_y_no_repite_pipeline(entorno):
    _, sdk, crear, reloj = entorno
    proveedor = crear()
    inicio = reloj[0]
    sdk.errores["get"].extend([error(429, 2), error(429, 2)])
    with pytest.raises(StorageNotFound):
        proveedor.get("demo/prueba")
    llamadas = [c for c in sdk.calls if c[0] == "get"]
    assert len(llamadas) == 3 and reloj[0] - inicio == 4
    assert len({c[2]["opc_client_request_id"] for c in llamadas}) == 1


def test_ack_perdido_se_reconcilia_sin_otro_put(entorno):
    _, sdk, crear, _ = entorno
    proveedor = crear()
    sdk.perder_ack = True
    objeto = proveedor.upload("demo/prueba", b"original", crear_solo=True)
    assert objeto.proveedor == "oci" and proveedor.get("demo/prueba") == b"original"
    assert len([c for c in sdk.calls if c[0] == "put"]) == 1
    assert proveedor.presupuesto.resumen()["reservas_inciertas"] == 0


def test_put_tres_intentos_misma_clave_payload_nonce_y_presupuesto(entorno):
    _, sdk, crear, _ = entorno
    proveedor = crear()
    sdk.errores["put"].extend([error(503)] * 3)
    with pytest.raises(StorageUnavailable):
        proveedor.upload("demo/prueba", b"original", crear_solo=True)
    llamadas = [c for c in sdk.calls if c[0] == "put"]
    assert len(llamadas) == 3 and len([c for c in sdk.calls if c[0] == "get"]) == 3
    assert all(c[1][2:] == ("demo/prueba", b"original") for c in llamadas)
    assert len({c[2]["opc_meta"]["nm-operation"] for c in llamadas}) == 1
    assert len({c[2]["opc_client_request_id"] for c in llamadas}) == 1
    assert proveedor.presupuesto.resumen()["solicitudes"] == len(sdk.calls)
    assert proveedor.presupuesto.resumen()["reservas_inciertas"] == 1


def test_verificacion_fallida_no_repite_put_y_conserva_reserva(entorno):
    _, sdk, crear, _ = entorno
    proveedor = crear()
    sdk.errores["get"].extend([error(503)] * 3)
    with pytest.raises(StorageUnavailable):
        proveedor.upload("demo/prueba", b"original")
    assert len([c for c in sdk.calls if c[0] == "put"]) == 1
    assert proveedor.presupuesto.resumen()["reservas_inciertas"] == 1


def test_paginacion_exclusiva_sin_duplicados(entorno):
    _, _, crear, _ = entorno
    proveedor = crear()
    for nombre in ("demo/a", "demo/b", "demo/c"):
        proveedor.upload(nombre, nombre)
    uno = proveedor.list("demo/", limit=2)
    dos = proveedor.list("demo/", limit=2, cursor=uno.next_cursor)
    assert [o.object_name for o in uno.items + dos.items] == ["demo/a", "demo/b", "demo/c"]
    assert uno.next_cursor == "demo/b" and dos.next_cursor is None


def test_presupuesto_bloquea_solicitud_antes_del_sdk(entorno):
    cfg, sdk, crear, _ = entorno
    cfg.oci_max_requests_per_month = 4
    proveedor = crear()
    with pytest.raises(StorageBudgetExceeded):
        proveedor.get("demo/prueba")
    assert len(sdk.calls) == 4


def test_capacidad_se_reserva_y_error_ambiguo_no_la_libera(entorno):
    cfg, sdk, crear, _ = entorno
    cfg.oci_max_storage_bytes = 5
    proveedor = crear()
    sdk.errores["put"].extend([error(503)] * 3)
    with pytest.raises(StorageUnavailable):
        proveedor.upload("demo/a", b"12345")
    anteriores = len(sdk.calls)
    with pytest.raises(StorageBudgetExceeded):
        proveedor.upload("demo/b", b"1")
    assert len(sdk.calls) == anteriores and proveedor.presupuesto.resumen()["bytes_reservados"] == 5


def test_ledger_persistente_atomico_y_mes_nuevo(tmp_path):
    ruta = tmp_path / "oci.sqlite3"
    presupuesto = PresupuestoOCI(ruta, solicitudes=7)

    def reservar(_):
        try:
            presupuesto.reservar_solicitud()
            return True
        except StorageBudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(reservar, range(20))) == 7
    nuevo = PresupuestoOCI(ruta, solicitudes=7)
    assert nuevo.resumen()["solicitudes"] == 7 and nuevo.resumen()["alerta"]
    nuevo._mes = lambda: "2099-01"
    nuevo.reservar_solicitud()
    assert nuevo.resumen()["solicitudes"] == 1


@pytest.mark.parametrize("nombre", ["../secret", "/ruta", "demo/a\\b", "demo/../a", "demo/CON", "demo/espacio x"])
def test_nombres_invalidos_no_consumen_sdk(entorno, nombre):
    _, sdk, crear, _ = entorno
    proveedor = crear()
    anteriores = len(sdk.calls)
    for operar in (
        lambda: proveedor.get(nombre),
        lambda: proveedor.upload(nombre, "x"),
        lambda: proveedor.delete(nombre),
    ):
        with pytest.raises(StorageInvalidName):
            operar()
    assert len(sdk.calls) == anteriores


def test_mock_y_real_comparten_contrato_bajo_condiciones(entorno, tmp_path):
    _, _, crear, _ = entorno
    for proveedor in (crear(), LocalMockStorageProvider(tmp_path / "mock")):
        objeto = proveedor.upload("demo/a", b"x", crear_solo=True)
        with pytest.raises(StorageConflict):
            proveedor.upload("demo/a", b"x", crear_solo=True)
        with pytest.raises(StorageNotFound):
            proveedor.upload("demo/b", b"x", if_match=objeto.etag)
        assert proveedor.get("demo/a", if_match=objeto.etag) == b"x"
        proveedor.delete("demo/a")
        with pytest.raises(StorageNotFound):
            proveedor.delete("demo/a")


@pytest.mark.parametrize(
    "metodo,opciones", [("get", {"if_match": "incorrecto"}), ("upload", {"crear_solo": True, "if_match": "x"})]
)
def test_condiciones_invalidas_no_se_ignoran(entorno, metodo, opciones):
    _, _, crear, _ = entorno
    proveedor = crear()
    proveedor.upload("demo/a", b"x")
    if metodo == "get":
        with pytest.raises(StorageConflict):
            proveedor.get("demo/a", **opciones)
    else:
        with pytest.raises(ValueError):
            proveedor.upload("demo/a", b"y", **opciones)


def test_delete_ack_perdido_no_convierte_404_en_error_nuevo(entorno, monkeypatch):
    _, sdk, crear, _ = entorno
    proveedor = crear()
    proveedor.upload("demo/a", b"x")
    borrar = sdk.delete_object

    def perder(*args, **kw):
        borrar(*args, **kw)
        raise oci.exceptions.RequestException("ack perdido")

    monkeypatch.setattr(sdk, "delete_object", perder)
    proveedor.delete("demo/a")
    assert "demo/a" not in sdk.objetos
    assert len([c for c in sdk.calls if c[0] == "delete"]) == 2


def test_no_reintenta_delete_permanente(entorno):
    _, sdk, crear, _ = entorno
    proveedor = crear()
    sdk.errores["delete"].append(error(403))
    with pytest.raises(StorageUnavailable):
        proveedor.delete("demo/a")
    assert len([c for c in sdk.calls if c[0] == "delete"]) == 1


def test_lectura_interrumpida_se_reintenta_y_cierra_stream(entorno, monkeypatch):
    _, sdk, crear, _ = entorno
    proveedor = crear()
    proveedor.upload("demo/a", b"contenido")
    original = Flujo.iter_content
    intentos = []

    def interrumpir(self, chunk_size):
        if not intentos:
            intentos.append(1)
            yield b"parcial"
            raise oci.exceptions.RequestException("timeout durante descarga")
        yield from original(self, chunk_size)

    monkeypatch.setattr(Flujo, "iter_content", interrumpir)
    assert proveedor.get("demo/a") == b"contenido"
    assert all(s.cerrado for s in sdk.streams)


def test_original_remoto_se_incorpora_antes_de_reservar_upload(entorno):
    cfg, sdk, crear, _ = entorno
    cfg.oci_max_storage_bytes = 10
    sdk.objetos["demo/existente"] = dict(contenido=b"0123456789", etag="x", headers={"etag": "x"})
    proveedor = crear()
    with pytest.raises(StorageBudgetExceeded):
        proveedor.upload("demo/nuevo", b"1")
    assert [c[0] for c in sdk.calls] == ["regiones", "namespace", "bucket", "list"]


def test_presupuesto_agotado_durante_confirmacion_no_duplica_put(entorno):
    cfg, sdk, crear, _ = entorno
    cfg.oci_max_requests_per_month = 5
    proveedor = crear()
    with pytest.raises(StorageBudgetExceeded):
        proveedor.upload("demo/a", b"x")
    assert len(sdk.calls) == 5 and len([c for c in sdk.calls if c[0] == "put"]) == 1
    assert proveedor.presupuesto.resumen()["reservas_inciertas"] == 1


@pytest.mark.parametrize("limit", [0, 1001, True, 1.5])
def test_paginacion_limite_invalido_sin_llamada(entorno, limit):
    _, sdk, crear, _ = entorno
    proveedor = crear()
    anteriores = len(sdk.calls)
    with pytest.raises(ValueError):
        proveedor.list("demo/", limit=limit)
    assert len(sdk.calls) == anteriores


def test_lista_incompleta_falla_en_lugar_de_exponer_metadata(entorno, monkeypatch):
    _, sdk, crear, _ = entorno
    proveedor = crear()

    def incompleta(*args, **kwargs):
        return NS(data=NS(objects=[NS(name="demo/a", etag=None, size=None, time_modified=None)], next_start_with=None))

    monkeypatch.setattr(sdk, "list_objects", incompleta)
    with pytest.raises(StorageUnavailable):
        proveedor.list("demo/")


def test_sdk_async_y_confirmacion_gratuidad_se_rechazan(entorno, monkeypatch):
    cfg, sdk, crear, _ = entorno
    cfg.oci_always_free_confirmed = False
    with pytest.raises(StorageConfigError, match="Always Free"):
        crear()
    cfg.oci_always_free_confirmed = True

    async def asincrono(*args, **kw):
        return "no"

    monkeypatch.setattr(sdk, "get_object", asincrono)
    with pytest.raises(StorageConfigError, match="síncronos"):
        crear()
    assert sdk.calls == []


def test_timeout_preserva_retry_after_que_supera_deadline(entorno):
    _, sdk, crear, _ = entorno
    proveedor = crear()
    sdk.errores["get"].append(error(429, 600))
    with pytest.raises(StorageUnavailable, match="Retry-After"):
        proveedor.get("demo/a")
    assert len([c for c in sdk.calls if c[0] == "get"]) == 1


def test_huella_no_confirma_contenido_distinto(entorno, monkeypatch):
    _, sdk, crear, _ = entorno
    proveedor = crear()
    original = sdk.get_object

    def diferente(*args, **kw):
        r = original(*args, **kw)
        r.data.contenido = b"cambiado"
        return r

    monkeypatch.setattr(sdk, "get_object", diferente)
    with pytest.raises(StorageUnavailable, match="contenido"):
        proveedor.upload("demo/a", b"original")
    assert len([c for c in sdk.calls if c[0] == "put"]) == 1


def test_reconciliacion_sin_permiso_no_reintenta_escritura(entorno):
    _, sdk, crear, _ = entorno
    proveedor = crear()
    sdk.errores["put"].append(error(503))
    sdk.errores["get"].append(error(403))
    with pytest.raises(StorageUnavailable):
        proveedor.upload("demo/a", b"original")
    assert len([c for c in sdk.calls if c[0] == "put"]) == 1


def test_deadline_agotado_en_put_no_inicia_otra_solicitud(entorno, monkeypatch):
    _, sdk, crear, reloj = entorno
    proveedor = crear()
    escribir = sdk.put_object

    def terminar_tarde(*args, **kwargs):
        respuesta = escribir(*args, **kwargs)
        reloj[0] += 301
        return respuesta

    monkeypatch.setattr(sdk, "put_object", terminar_tarde)
    with pytest.raises(StorageUnavailable, match="deadline"):
        proveedor.upload("demo/tardio", b"persistido", crear_solo=True)
    assert len([c for c in sdk.calls if c[0] == "put"]) == 1
    assert not any(c[0] == "get" for c in sdk.calls)
    assert proveedor.presupuesto.resumen()["reservas_inciertas"] == 1
