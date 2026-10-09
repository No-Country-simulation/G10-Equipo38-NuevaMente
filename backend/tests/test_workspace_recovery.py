"""Pérdida de volumen, lápidas y actividad con storage explícitamente simulado."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from app.core.rag.rebuild import ManifiestoDocumento, _workspace_vigente
from app.jobs import store
from app.jobs.manager import TrabajoCanceladoError
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.responses import PedagogicalOutput
from app.session.manager import SessionManager
from app.session.manifests import sincronizar_pendientes
from app.session.recovery import restaurar_registro
from app.storage.object_keys import clave_documento, clave_original, clave_output, clave_progress, clave_workspace
from app.storage.oci_storage import LocalMockStorageProvider
from app.storage.provider import StorageUnavailable
from test_schemas import FLASHCARDS, salida_valida

pytestmark = pytest.mark.integration_mock


@pytest.fixture
def pila(tmp_path):
    db = RegistroOperativo(tmp_path / "original.sqlite3")
    storage = LocalMockStorageProvider(tmp_path / "oci")
    manager = SessionManager(db, storage, 30, 24)
    yield db, storage, manager
    db.cerrar()


def fuente(storage, workspace_id, document_id="doc_01HX"):
    original = b"Las redes VCN permiten organizar subredes privadas."
    manifest = ManifiestoDocumento(
        workspace_id=workspace_id,
        document_id=document_id,
        source_name="redes.txt",
        hash_sha256=hashlib.sha256(original).hexdigest(),
        estado=DocumentStatus.READY,
    )
    storage.upload(clave_original(workspace_id, document_id), original)
    storage.upload(clave_documento(workspace_id, document_id), manifest.model_dump_json())
    return manifest


def paquete(storage, workspace_id, doc, generation_id="gen_01HX"):
    datos = salida_valida(FLASHCARDS).model_dump(mode="json")
    datos["generation_id"] = generation_id
    datos["documento_fuente"]["document_id"] = doc.document_id
    datos["documento_fuente"]["hash"] = doc.hash_sha256
    datos["almacenamiento_oci"]["objeto_id"] = clave_output(workspace_id, generation_id)
    output = PedagogicalOutput.model_validate(datos)
    storage.upload(clave_output(workspace_id, generation_id), output.model_dump_json())
    return output


def test_borrado_durable_no_se_restaura_en_volumen_nuevo(pila, tmp_path):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    fuente(storage, creado.workspace_id)
    manager.borrar_workspace(creado.workspace_id)
    manifest = json.loads(storage.get(clave_workspace(creado.workspace_id)))
    assert manifest["borrado"] and manifest["borrado_en"]
    assert db.listar_manifiestos_pendientes() == []
    ctx = SimpleNamespace(workspace_id=creado.workspace_id, chequear=lambda: None)
    with pytest.raises(TrabajoCanceladoError):
        _workspace_vigente(storage, ctx)
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        assert restaurar_registro(nuevo, storage) == 1
        assert nuevo.resolver_workspace(creado.recovery_code) is None
        assert nuevo.obtener_sesion(creado.token) is None
        assert nuevo.obtener_documento("doc_01HX") is None
        assert nuevo.esta_borrado("workspace", creado.workspace_id)
    finally:
        nuevo.cerrar()


def test_fallo_oci_bloquea_y_reintento_persiste_la_misma_lapida(pila, monkeypatch):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    upload = storage.upload

    def fallo(*args, **kwargs):
        raise StorageUnavailable("detalle-privado-del-proveedor")

    monkeypatch.setattr(storage, "upload", fallo)
    manager.borrar_workspace(creado.workspace_id)
    assert db.obtener_sesion(creado.token) is None
    assert db.listar_manifiestos_pendientes() == [creado.workspace_id]
    version = db.workspace_para_manifest(creado.workspace_id)["version_manifiesto"]
    monkeypatch.setattr(storage, "upload", upload)
    assert sincronizar_pendientes(db, storage) == 1
    manifest = json.loads(storage.get(clave_workspace(creado.workspace_id)))
    assert manifest["borrado"] and manifest["version"] == version
    assert db.listar_manifiestos_pendientes() == []


def test_confirmacion_ambigua_se_reconcilia_sin_otra_escritura(pila, monkeypatch):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    upload = storage.upload
    llamadas = []

    def ambiguo(*args, **kwargs):
        llamadas.append(1)
        upload(*args, **kwargs)
        raise StorageUnavailable("respuesta perdida después de escribir")

    monkeypatch.setattr(storage, "upload", ambiguo)
    manager.borrar_workspace(creado.workspace_id)
    assert db.listar_manifiestos_pendientes() == [creado.workspace_id]
    assert sincronizar_pendientes(db, storage) == 1
    assert len(llamadas) == 1


def test_actividad_renueva_espacio_agrupada_sin_prolongar_token(pila, monkeypatch):
    db, storage, manager = pila
    reloj = [datetime(2026, 10, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(store, "_ahora", lambda: reloj[0])
    creado = manager.crear_espacio()
    token_expira = db.obtener_sesion(creado.token)["expira_en"]
    reloj[0] += timedelta(hours=12)
    assert manager.obtener_sesion(creado.token)
    actualizado = db.obtener_workspace(creado.workspace_id)
    assert datetime.fromisoformat(actualizado["expira_en"]) == reloj[0] + timedelta(days=30)
    version = actualizado["version_manifiesto"]
    for _ in range(10):
        assert manager.obtener_sesion(creado.token)
    assert db.obtener_workspace(creado.workspace_id)["version_manifiesto"] == version
    assert db.obtener_sesion(creado.token)["expira_en"] == token_expira
    assert sincronizar_pendientes(db, storage) == 1
    assert json.loads(storage.get(clave_workspace(creado.workspace_id)))["version"] == version
    reloj[0] += timedelta(hours=13)
    assert manager.obtener_sesion(creado.token) is None


def test_rotacion_no_tiene_exito_si_borraron_tras_la_lectura(pila, monkeypatch):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    obtener = db.obtener_workspace

    def leer_y_borrar(workspace_id):
        vigente = obtener(workspace_id)
        db.borrar_workspace(workspace_id)
        return vigente

    monkeypatch.setattr(db, "obtener_workspace", leer_y_borrar)
    with pytest.raises(ErrorAplicacion) as exc:
        manager.rotar_credenciales(creado.workspace_id)
    assert exc.value.error.code == ErrorCode.INVALID_STATE
    assert db.obtener_sesion(creado.token) is None
    assert sincronizar_pendientes(db, storage) == 1
    assert json.loads(storage.get(clave_workspace(creado.workspace_id)))["borrado"]


def test_perdida_volumen_recupera_recursos_y_codigo_sin_sesiones(pila, tmp_path):
    _, storage, manager = pila
    creado = manager.crear_espacio()
    doc = fuente(storage, creado.workspace_id)
    output = paquete(storage, creado.workspace_id, doc)
    state = {"version": 2, "eventos": [{"generation_id": output.generation_id, "event_id": "evt-1"}]}
    storage.upload(clave_progress(creado.workspace_id), json.dumps(state))
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        assert restaurar_registro(nuevo, storage) == 1
        assert nuevo.resolver_workspace(creado.recovery_code)["workspace_id"] == creado.workspace_id
        assert nuevo.obtener_sesion(creado.token) is None
        assert nuevo.obtener_documento(doc.document_id)["estado"] == "processing"
        assert nuevo.obtener_generacion(output.generation_id)["status"] == "completed"
        assert nuevo.obtener_progreso_restaurado(creado.workspace_id) == state
        sesion = SessionManager(nuevo, storage, 30, 24).recuperar_sesion(creado.recovery_code)
        assert nuevo.obtener_sesion(sesion.token)
        assert restaurar_registro(nuevo, storage) == 0
    finally:
        nuevo.cerrar()


def test_lapidas_de_documento_generacion_y_progreso_no_reaparecen(pila, tmp_path):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    retirado = fuente(storage, creado.workspace_id)
    vigente = fuente(storage, creado.workspace_id, "doc-vigente")
    paquete(storage, creado.workspace_id, retirado)
    paquete(storage, creado.workspace_id, vigente, "gen-vigente")
    db.agendar_borrado("document", retirado.document_id, creado.workspace_id)
    db.agendar_borrado("generation", "gen-vigente", creado.workspace_id)
    sincronizar_pendientes(db, storage)
    storage.upload(
        clave_progress(creado.workspace_id),
        json.dumps(
            {
                "version": 1,
                "eventos": [
                    {"generation_id": "gen_01HX", "event_id": "evt-retirado"},
                    {"generation_id": "gen-vigente", "event_id": "evt-retirado-2"},
                ],
            }
        ),
    )
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        restaurar_registro(nuevo, storage)
        assert nuevo.obtener_documento(retirado.document_id) is None
        assert nuevo.obtener_documento(vigente.document_id)
        assert nuevo.obtener_generacion("gen_01HX") is None
        assert nuevo.obtener_generacion("gen-vigente") is None
        assert nuevo.obtener_progreso_restaurado(creado.workspace_id)["eventos"] == []
        assert nuevo.esta_borrado("document", retirado.document_id)
    finally:
        nuevo.cerrar()


def test_conflicto_oci_no_sobrescribe_version_mas_nueva(pila):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    key = clave_workspace(creado.workspace_id)
    remoto = json.loads(storage.get(key))
    remoto["version"] = 100
    storage.upload(key, json.dumps(remoto))
    with pytest.raises(ErrorAplicacion) as exc:
        manager.rotar_credenciales(creado.workspace_id)
    assert exc.value.error.code == ErrorCode.INVALID_STATE
    assert db.resolver_workspace(creado.recovery_code)
    assert db.obtener_sesion(creado.token)
    assert json.loads(storage.get(key))["version"] == 100


def test_confirmacion_vieja_no_borra_actividad_nueva(pila, monkeypatch):
    db, storage, manager = pila
    reloj = [datetime(2026, 10, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(store, "_ahora", lambda: reloj[0])
    creado = manager.crear_espacio()
    reloj[0] += timedelta(hours=1)
    manager.obtener_sesion(creado.token)
    anterior = db.obtener_workspace(creado.workspace_id)["version_manifiesto"]
    reloj[0] += timedelta(hours=1)
    manager.obtener_sesion(creado.token)
    db.confirmar_manifiesto(creado.workspace_id, anterior)
    assert db.listar_manifiestos_pendientes() == [creado.workspace_id]


def test_restauracion_interrumpida_se_retoma_sin_duplicar(pila, tmp_path, monkeypatch):
    _, storage, manager = pila
    creados = [manager.crear_espacio(), manager.crear_espacio()]
    ordenados = sorted(creados, key=lambda item: item.workspace_id)
    get = storage.get
    fallo = [True]

    def interrumpido(key, **kwargs):
        if key == clave_workspace(ordenados[1].workspace_id) and fallo[0]:
            raise StorageUnavailable("interrupción simulada")
        return get(key, **kwargs)

    monkeypatch.setattr(storage, "get", interrumpido)
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        with pytest.raises(StorageUnavailable):
            restaurar_registro(nuevo, storage)
        assert nuevo.recuperacion_pendiente()
        assert nuevo.resolver_workspace(ordenados[0].recovery_code)
        fallo[0] = False
        assert restaurar_registro(nuevo, storage) == 1
        assert not nuevo.recuperacion_pendiente()
        assert all(nuevo.resolver_workspace(item.recovery_code) for item in creados)
    finally:
        nuevo.cerrar()


def test_registro_existente_y_lapidas_locales_no_se_sobrescriben(pila):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    db.borrar_workspace(creado.workspace_id)
    restaurar_registro(db, storage)
    assert db.resolver_workspace(creado.recovery_code) is None
    assert db.esta_borrado("workspace", creado.workspace_id)


def test_original_inconsistente_impide_publicar_registros(pila, tmp_path):
    _, storage, manager = pila
    creado = manager.crear_espacio()
    doc = fuente(storage, creado.workspace_id)
    storage.upload(clave_original(creado.workspace_id, doc.document_id), b"otro contenido")
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        with pytest.raises(ValueError, match="Original distinto"):
            restaurar_registro(nuevo, storage)
        assert nuevo.registro_vacio()
        assert nuevo.recuperacion_pendiente()
    finally:
        nuevo.cerrar()


def test_manifesto_heredado_sin_actividad_es_recuperable(pila, tmp_path):
    _, storage, manager = pila
    creado = manager.crear_espacio()
    key = clave_workspace(creado.workspace_id)
    legacy = json.loads(storage.get(key))
    for campo in ("ultima_actividad_en", "borrado", "borrado_en", "recursos_borrados"):
        legacy.pop(campo)
    storage.upload(key, json.dumps(legacy))
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        assert restaurar_registro(nuevo, storage) == 1
        assert nuevo.resolver_workspace(creado.recovery_code)
        assert nuevo.obtener_sesion(creado.token) is None
    finally:
        nuevo.cerrar()
