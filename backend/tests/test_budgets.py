"""#50: presupuestos persistentes, reservas concurrentes y revocación/limpieza."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import test_storage_oci as oci_pruebas
from app.config import Configuracion
from app.documents.admission import ServicioAdmisionDocumental
from app.jobs.manager import GestorTrabajos
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.errors import ErrorAplicacion
from app.schemas.requests import UploadRequest
from app.session.lifecycle import CicloVidaEspacios
from app.session.manager import SessionManager
from app.session.manifests import sincronizar_manifiesto
from app.session.recovery import restaurar_registro
from app.storage.budgeted import StoragePresupuestado
from app.storage.object_keys import clave_workspace
from app.storage.oci_budget import PresupuestoOCI, mantenimiento_storage
from app.storage.oci_storage import LocalMockStorageProvider
from app.storage.provider import StorageBudgetExceeded, StorageConfigError, StorageUnavailable
from test_jobs_manager import esperarlo

pytestmark = pytest.mark.unit
entorno_oci = oci_pruebas.entorno


@pytest.fixture
def espacio(tmp_path):
    db = RegistroOperativo(tmp_path / "state.sqlite3")
    storage = LocalMockStorageProvider(tmp_path / "storage")
    sesiones = SessionManager(db, storage, 30, 24)
    ws = sesiones.crear_espacio()
    efectos = SimpleNamespace(_lock=threading.RLock(), indice=None)
    ciclo = CicloVidaEspacios(db, storage, efectos, Mock())
    yield SimpleNamespace(db=db, storage=storage, sesiones=sesiones, ws=ws, ciclo=ciclo)
    db.cerrar()


def test_80_capacidad_limite_y_limpieza_aun_bloqueadas_nuevas_escrituras(tmp_path):
    ledger = PresupuestoOCI(tmp_path / "budget", solicitudes=20, bytes_maximos=100)
    storage = StoragePresupuestado(LocalMockStorageProvider(tmp_path / "storage"), ledger)
    storage.upload("demo/uno", b"a" * 80)
    assert ledger.resumen()["alerta"]
    with pytest.raises(StorageBudgetExceeded):
        storage.upload("demo/dos", b"b" * 20)  # 1% reservado a lápidas.
    while ledger.resumen()["solicitudes"] < 18:
        storage.get("demo/uno")
    with pytest.raises(StorageBudgetExceeded):
        storage.upload("demo/dos", b"b")
    with mantenimiento_storage():
        storage.delete("demo/uno")
        assert not storage.list("demo/").items
    resumen = ledger.resumen()
    assert resumen["bytes_reservados"] == 0 and resumen["solicitudes"] == 20
    assert resumen["operaciones"] == {"put_object": 1, "get_object": 17, "delete_object": 1, "list_objects": 1}
    with mantenimiento_storage(), pytest.raises(StorageBudgetExceeded):
        storage.list()
    nuevo = PresupuestoOCI(tmp_path / "budget", solicitudes=20, bytes_maximos=100)
    assert nuevo.resumen() == resumen


def test_ampliar_presupuesto_conserva_consumo_y_margen(tmp_path):
    ruta = tmp_path / "budget"
    anterior = PresupuestoOCI(ruta, solicitudes=5000)
    anterior.reservar_solicitud("get_object")
    nuevo = PresupuestoOCI(ruta)
    resumen = nuevo.resumen()
    assert resumen["solicitudes"] == 1
    assert resumen["operaciones"] == {"get_object": 1}
    assert resumen["limite_solicitudes"] == 20000
    assert resumen["limite_operaciones_usuario"] == 18000
    assert resumen["margen_limpieza"] == 2000
    assert Configuracion(_env_file=None).oci_max_requests_per_month == 20000
    with pytest.raises(ValueError):
        PresupuestoOCI(ruta, solicitudes=20001)
    with pytest.raises(ValueError):
        Configuracion(_env_file=None, oci_max_requests_per_month=20001)


def test_reservas_concurrentes_no_sobrevenden_y_reemplazo_libera_bytes(tmp_path):
    ledger = PresupuestoOCI(tmp_path / "budget", bytes_maximos=10)

    def reservar(n):
        try:
            ledger.reservar_upload(str(n), "demo/" + str(n), 4)
            return True
        except StorageBudgetExceeded:
            return False

    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(reservar, range(20))) == 2
    assert ledger.resumen()["bytes_reservados"] == 8
    for n in range(20):
        ledger.liberar_reserva(str(n))
    ledger.reservar_upload("a", "demo/a", 10)
    ledger.confirmar_upload("a", "demo/a", 10)
    ledger.reservar_upload("b", "demo/a", 5)
    ledger.confirmar_upload("b", "demo/a", 5)
    assert ledger.resumen()["bytes_reservados"] == 5


def test_cinco_documentos_se_reservan_antes_de_persistir_y_reintento_no_consume(espacio):
    e = espacio
    gestor = GestorTrabajos(e.db)
    try:
        admision = ServicioAdmisionDocumental(e.db, e.storage, gestor, lambda ctx, doc: None)
        for n in range(5):
            recibo = admision.admitir_texto(
                e.ws.token, UploadRequest(documento_titulo="Red", documento_contenido="Una red"), clave=str(n)
            )
            esperarlo(e.db, recibo.job_id, JobStatus.COMPLETED, plazo=5)
        assert (
            admision.admitir_texto(
                e.ws.token, UploadRequest(documento_titulo="Red", documento_contenido="Una red"), clave="4"
            ).document_id
            == recibo.document_id
        )
        antes = e.storage.list().items
        with pytest.raises(ErrorAplicacion) as error:
            admision.admitir_texto(
                e.ws.token, UploadRequest(documento_titulo="Red", documento_contenido="Otra red"), clave="6"
            )
        assert error.value.status_code == 429 and error.value.error.details["limite"] == 5
        assert e.storage.list().items == antes
        e.db.agendar_borrado("document", recibo.document_id, e.ws.workspace_id)
        assert e.db.uso_espacio(e.ws.workspace_id)["documentos"] == 4
    finally:
        gestor.detener()


def test_veinte_generaciones_y_lapida_libera_plaza(espacio):
    e = espacio
    e.db.registrar_documento("doc_test", e.ws.workspace_id, "Red", DocumentStatus.READY)
    for n in range(20):
        with e.db.transaccion():
            e.db.comprobar_limite_recursos(e.ws.workspace_id, "generations", 20)
            e.db.registrar_generacion("gen_" + str(n), e.ws.workspace_id, "doc_test")
    with pytest.raises(ErrorAplicacion) as error:
        e.db.comprobar_limite_recursos(e.ws.workspace_id, "generations", 20)
    assert error.value.error.details["limite"] == 20
    e.db.agendar_borrado("generation", "gen_19", e.ws.workspace_id)
    e.db.comprobar_limite_recursos(e.ws.workspace_id, "generations", 20)


def test_actividad_renueva_30_dias_y_expiracion_no_resucita_al_restaurar(espacio, tmp_path, monkeypatch):
    e = espacio
    ahora = datetime.now(timezone.utc)
    futuro = ahora + timedelta(hours=1)
    monkeypatch.setattr("app.jobs.store._ahora", lambda: futuro)
    assert e.sesiones.obtener_sesion(e.ws.token)
    nuevo = e.db.obtener_workspace(e.ws.workspace_id)
    assert datetime.fromisoformat(nuevo["expira_en"]) == futuro + timedelta(days=30)
    sincronizar_manifiesto(e.db, e.storage, e.ws.workspace_id)
    assert json.loads(e.storage.get(clave_workspace(e.ws.workspace_id)))["expira_en"].startswith(
        (futuro + timedelta(days=30)).date().isoformat()
    )
    monkeypatch.setattr("app.jobs.store._ahora", lambda: futuro + timedelta(days=31))
    assert e.sesiones.obtener_sesion(e.ws.token) is None
    e.ciclo.reconciliar()
    reconstruido = RegistroOperativo(tmp_path / "restaurado.sqlite3")
    try:
        restaurar_registro(reconstruido, e.storage)
        assert reconstruido.obtener_workspace(e.ws.workspace_id) is None
        assert reconstruido.esta_borrado("workspace", e.ws.workspace_id)
    finally:
        reconstruido.cerrar()


def test_borrado_revoca_ante_fallo_storage_y_reintenta_sin_resurreccion(espacio, tmp_path, monkeypatch):
    e = espacio
    for raiz in ("source_documents", "outputs", "exports", "progress"):
        e.storage.upload(f"{raiz}/{e.ws.workspace_id}/prueba", b"privado")
    eliminar = e.storage.delete
    monkeypatch.setattr(e.storage, "delete", lambda *a: (_ for _ in ()).throw(StorageUnavailable("privado")))
    e.sesiones.borrar_workspace(e.ws.workspace_id)
    assert e.sesiones.obtener_sesion(e.ws.token) is None
    e.ciclo.reconciliar()
    fallo = e.db.limpiezas_espacios()[0]
    assert fallo["estado"] == "pendiente" and fallo["error_code"] == "StorageUnavailable"
    restaurado = RegistroOperativo(tmp_path / "restaurado.sqlite3")
    try:
        restaurar_registro(restaurado, e.storage)
        assert restaurado.obtener_workspace(e.ws.workspace_id) is None
    finally:
        restaurado.cerrar()
    monkeypatch.setattr(e.storage, "delete", eliminar)
    e.ciclo.reconciliar()
    assert e.db.limpiezas_espacios()[0]["estado"] == "datos_purgados"
    assert e.db.limpiezas_espacios()[0]["error_code"] is None
    assert [o.object_name for o in e.storage.list().items] == [clave_workspace(e.ws.workspace_id)]


def test_retencion_solo_puede_reducirse():
    with pytest.raises(ValueError):
        Configuracion(workspace_retention_days=31)


def test_bucket_rechaza_transiciones_habilitadas(entorno_oci):
    _, sdk, crear, _ = entorno_oci
    sdk.get_object_lifecycle_policy = lambda *a, **k: NS(data=NS(items=[NS(is_enabled=True, action="ARCHIVE")]))
    with pytest.raises(StorageConfigError, match="transiciones"):
        crear()


def test_reinicio_al_limite_ordinario_conserva_capacidad_de_limpieza(entorno_oci):
    cfg, sdk, crear, _ = entorno_oci
    cfg.oci_max_requests_per_month = 100
    proveedor = crear()
    while proveedor.presupuesto.resumen()["solicitudes"] < 90:
        proveedor.presupuesto.reservar_solicitud("prueba")
    reiniciado = crear()
    assert reiniciado.presupuesto.resumen()["solicitudes"] == 95
    with pytest.raises(StorageBudgetExceeded):
        reiniciado.get("demo/inexistente")
    from app.storage.provider import StorageNotFound

    with mantenimiento_storage(), pytest.raises(StorageNotFound):
        reiniciado.delete("demo/inexistente")
    assert reiniciado.presupuesto.resumen()["solicitudes"] == 96


def test_limites_configurables_validacion_al_arranque():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Configuracion(workspace_max_documents=0)
    with pytest.raises(ValidationError):
        Configuracion(workspace_max_documents=-1)
    with pytest.raises(ValidationError):
        Configuracion(workspace_max_documents=101)
    with pytest.raises(ValidationError):
        Configuracion(workspace_max_generations=0)
    with pytest.raises(ValidationError):
        Configuracion(workspace_max_generations=501)

    cfg = Configuracion(workspace_max_documents=10, workspace_max_generations=50)
    assert cfg.workspace_max_documents == 10
    assert cfg.workspace_max_generations == 50


def test_registro_operativo_rechaza_limites_no_positivos(tmp_path):
    with pytest.raises(ValueError, match="enteros positivos"):
        RegistroOperativo(tmp_path / "inv1.db", limite_documentos=0)
    with pytest.raises(ValueError, match="enteros positivos"):
        RegistroOperativo(tmp_path / "inv2.db", limite_generaciones=-5)
    with pytest.raises(ValueError, match="enteros positivos"):
        RegistroOperativo(tmp_path / "inv3.db", limite_documentos=True)
    with pytest.raises(ValueError, match="enteros positivos"):
        RegistroOperativo(tmp_path / "inv4.db", limite_generaciones=2.5)
    with pytest.raises(ValueError, match="enteros positivos"):
        RegistroOperativo(tmp_path / "inv5.db", max_documentos=False)
