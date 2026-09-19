"""Tests del registro operativo SQLite (issue #08).

Verificación exacta del issue: `pytest backend/tests/test_jobs_store.py`,
que incluye el test de reinicio SIMULADO: se abre un store, se escribe
estado, se cierra (como un proceso que termina), y se abre UNA INSTANCIA
NUEVA sobre el mismo archivo — que es exactamente lo que pasa tras un
reinicio real del backend.

Criterio por criterio:

1. Reinicio conserva workspaces/sesiones/trabajos  -> test_reinicio_conserva_...
2. Misma Idempotency-Key + mismo cuerpo -> misma respuesta sin duplicar;
   cuerpo distinto -> conflicto (409)    -> test_idempotencia_...
3. Trabajo `running` al matar el proceso -> `failed` al reiniciar
                                            -> test_trabajo_running_...

Además: hashes en lugar de secretos (§7.4, verificado leyendo los bytes
crudos del archivo), validez de sesiones (expiración/revocación/borrado
del espacio), tombstones que sobreviven al reinicio, eventos monotónicos
con reconexión Last-Event-ID (§3.3) y migraciones versionadas idempotentes.

Nota para quien no conozca pytest: el fixture `store` crea la base en un
directorio temporal fresco por test (pytest la borra al terminar). La
marca `unit` indica que no hay red ni servicios externos: SQLite es un
archivo local.
"""

from pathlib import Path

import pytest
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus, JobStatus

pytestmark = pytest.mark.unit


@pytest.fixture
def ruta_db(tmp_path) -> Path:
    """Ruta del archivo de base en un directorio temporal del test."""
    return tmp_path / "registro_operativo.sqlite3"


@pytest.fixture
def store(ruta_db) -> RegistroOperativo:
    """Instancia cerrada automáticamente al terminar el test."""
    registro = RegistroOperativo(ruta_db)
    yield registro
    registro.cerrar()


def _espacio_de_ejemplo(registro: RegistroOperativo) -> str:
    """Espacio + documento + generación en estados intermedios, para los tests."""
    registro.crear_workspace("ws_1", "CODIGO-DE-RECUPERACION-1234", dias_validez=30)
    registro.registrar_documento("doc_1", "ws_1", "Manual VCN", DocumentStatus.PROCESSING)
    registro.registrar_generacion("gen_1", "ws_1", "doc_1", JobStatus.QUEUED)
    return "ws_1"


# ---------------------------------------------------------------------------
# Criterio 1: el reinicio conserva el estado
# ---------------------------------------------------------------------------


def test_reinicio_conserva_workspace_sesion_y_trabajos(ruta_db):
    """Criterio 1: cerrar y REABRIR la base conserva todo (§7.2 y §14.2)."""
    # --- "primer proceso": crea estado y termina ---
    primero = RegistroOperativo(ruta_db)
    _espacio_de_ejemplo(primero)
    primero.crear_sesion("TOKEN-SESION-CLARO", "ws_1", horas_validez=24)
    primero.actualizar_documento("doc_1", DocumentStatus.READY, hash_documento="sha256:abc")
    primero.actualizar_generacion("gen_1", JobStatus.COMPLETED)
    primero.cerrar()

    # --- "segundo proceso" (post reinicio): instancia nueva, mismo archivo ---
    segundo = RegistroOperativo(ruta_db)

    assert segundo.obtener_workspace("ws_1") is not None
    assert segundo.obtener_documento("doc_1")["estado"] == "ready"
    assert segundo.obtener_generacion("gen_1")["status"] == "completed"
    # La sesión sigue válida tras el reinicio (no se "invalida" sola).
    sesion = segundo.obtener_sesion("TOKEN-SESION-CLARO")
    assert sesion is not None and sesion["workspace_id"] == "ws_1"
    segundo.cerrar()


# ---------------------------------------------------------------------------
# Criterio 3: running interrumpido -> failed/INTERRUPTED
# ---------------------------------------------------------------------------


def test_trabajo_running_queda_failed_tras_reinicio(ruta_db):
    """Criterio 3: matar el proceso con un trabajo running deja el trabajo
    como failed con causa INTERRUPTED cuando la base se vuelve a abrir."""
    primero = RegistroOperativo(ruta_db)
    _espacio_de_ejemplo(primero)
    primero.actualizar_generacion("gen_1", JobStatus.RUNNING)
    # Simulación del "kill": NO hay cierre prolijo de negocio (el commit de
    # SQLite ya persistió el estado running; el proceso simplemente desaparece).
    primero.cerrar()

    segundo = RegistroOperativo(ruta_db)
    trabajo = segundo.obtener_generacion("gen_1")
    assert trabajo["status"] == "failed"
    assert trabajo["error_code"] == "INTERRUPTED"
    segundo.cerrar()


def test_la_recuperacion_solo_toca_los_running(ruta_db):
    """Los queued no se tocan acá (reencolar es decisión del gestor #20) y
    los terminales (completed/failed) permanecen intactos."""
    primero = RegistroOperativo(ruta_db)
    _espacio_de_ejemplo(primero)
    primero.registrar_generacion("gen_2", "ws_1", "doc_1", JobStatus.RUNNING)
    primero.registrar_generacion("gen_3", "ws_1", "doc_1", JobStatus.QUEUED)
    primero.actualizar_generacion("gen_1", JobStatus.COMPLETED)
    primero.cerrar()

    segundo = RegistroOperativo(ruta_db)
    assert segundo.obtener_generacion("gen_1")["status"] == "completed"  # terminal intacto
    assert segundo.obtener_generacion("gen_2")["status"] == "failed"  # running -> failed
    assert segundo.obtener_generacion("gen_3")["status"] == "queued"  # queued intacto
    segundo.cerrar()


# ---------------------------------------------------------------------------
# Criterio 2: idempotencia (§7.3)
# ---------------------------------------------------------------------------


def test_idempotencia_misma_clave_mismo_cuerpo_devuelve_mismo_recurso(store):
    """Criterio 2a: el reintento NO duplica el recurso; devuelve el mismo id."""
    cuerpo = '{"document_id": "doc_1", "formato_salida": "flashcards"}'

    resultado_1, recurso_1 = store.idempotencia_iniciar("ws_1", "generate", "clave-1", cuerpo, recurso_id="gen_1")
    assert resultado_1 == "creada"
    # El trabajo "real" crea el recurso y cierra la intención.
    store.idempotencia_completar("ws_1", "generate", "clave-1", "gen_1")

    # Reintento idéntico (misma clave, mismo cuerpo): MISMO identificador.
    resultado_2, recurso_2 = store.idempotencia_iniciar("ws_1", "generate", "clave-1", cuerpo)
    assert resultado_2 == "duplicada"
    assert recurso_2 == "gen_1"


def test_idempotencia_misma_clave_cuerpo_distinto_conflicto(store):
    """Criterio 2b: misma clave con otro cuerpo -> conflicto (la API: 409)."""
    store.idempotencia_iniciar("ws_1", "generate", "clave-1", '{"formato": "quiz"}', recurso_id="gen_1")
    resultado, recurso = store.idempotencia_iniciar("ws_1", "generate", "clave-1", '{"formato": "flashcards"}')
    assert resultado == "conflicto"
    assert recurso is None


def test_idempotencia_duplicada_en_curso_con_recurso_reservado(store):
    """Un reintento mientras la primera intención sigue en curso devuelve
    'duplicada' con el mismo recurso reservado: no crea otro."""
    cuerpo = "{}"
    store.idempotencia_iniciar("ws_1", "upload", "clave-up", cuerpo, recurso_id="doc_1")
    resultado, recurso = store.idempotencia_iniciar("ws_1", "upload", "clave-up", cuerpo)
    assert resultado == "duplicada"
    assert recurso == "doc_1"


def test_idempotencia_acotada_por_workspace_y_operacion(store):
    """La terna es (workspace, operación, clave): la misma clave en OTRO
    espacio u otra operación es una intención distinta (§7.3)."""
    store.crear_workspace("ws_2", "CODIGO-2", dias_validez=30)
    assert store.idempotencia_iniciar("ws_1", "generate", "k", "{}", "gen_1")[0] == "creada"
    assert store.idempotencia_iniciar("ws_2", "generate", "k", "{}", "gen_2")[0] == "creada"  # otro espacio
    assert store.idempotencia_iniciar("ws_1", "upload", "k", "{}", "doc_2")[0] == "creada"  # otra operación


def test_idempotencia_sobrevive_al_reinicio(ruta_db):
    """La clave viva tras un reinicio sigue siendo la misma intención."""
    cuerpo = '{"a": 1}'
    primero = RegistroOperativo(ruta_db)
    primero.crear_workspace("ws_1", "C", dias_validez=30)
    primero.idempotencia_iniciar("ws_1", "generate", "k", cuerpo, recurso_id="gen_9")
    primero.idempotencia_completar("ws_1", "generate", "k", "gen_9")
    primero.cerrar()

    segundo = RegistroOperativo(ruta_db)
    resultado, recurso = segundo.idempotencia_iniciar("ws_1", "generate", "k", cuerpo)
    assert (resultado, recurso) == ("duplicada", "gen_9")
    segundo.cerrar()


# ---------------------------------------------------------------------------
# §7.4: jamás secretos en claro dentro del archivo
# ---------------------------------------------------------------------------


def test_la_base_no_guarda_secretos_en_claro(ruta_db):
    """El archivo de la base NO contiene el código ni el token en claro,
    aunque sí sus hashes (verificamos los BYTES crudos del archivo)."""
    registro = RegistroOperativo(ruta_db)
    registro.crear_workspace("ws_1", "CODIGO-DE-RECUPERACION-1234", dias_validez=30)
    registro.crear_sesion("TOKEN-SESION-CLARO", "ws_1", horas_validez=24)
    registro.idempotencia_iniciar("ws_1", "generate", "k", "{}", recurso_id="gen_1")
    with pytest.raises(ValueError, match="credenciales"):
        registro.idempotencia_completar(
            "ws_1", "generate", "k", "gen_1", respuesta={"recovery_code": "CODIGO-DE-RECUPERACION-1234"}
        )
    registro.cerrar()

    bytes_crudos = ruta_db.read_bytes()
    assert b"CODIGO-DE-RECUPERACION-1234" not in bytes_crudos  # el código nunca viaja
    assert b"TOKEN-SESION-CLARO" not in bytes_crudos
    # Y el espacio sigue verificable con el código correcto (hash match).
    registro = RegistroOperativo(ruta_db)
    assert registro.obtener_workspace("ws_1") is not None
    registro.cerrar()


# ---------------------------------------------------------------------------
# Sesiones: validez, revocación y borrado del espacio
# ---------------------------------------------------------------------------


def test_sesion_expirada_y_revocada_no_sirven(store):
    from datetime import datetime, timedelta, timezone

    from app.jobs.store import _hash_de

    store.crear_workspace("ws_1", "C1", dias_validez=30)
    store.crear_sesion("token-valido", "ws_1", horas_validez=24)
    store.crear_sesion("token-viejo", "ws_1", horas_validez=1)

    # Simular que pasaron 2 horas: vencer TODAS las sesiones y re-frescar
    # solo la que el test quiere mantener vigente.
    pasado = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    futuro = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    with store._lock:  # el test manipula la base como lo haría el tiempo
        store._conn.execute("UPDATE sessions SET expira_en = ?", (pasado,))
        store._conn.execute(
            "UPDATE sessions SET expira_en = ? WHERE token_hash = ?",
            (futuro, _hash_de("token-valido")),
        )

    assert store.obtener_sesion("token-viejo") is None  # expirada
    assert store.obtener_sesion("token-valido") is not None

    # Revocada: deja de servir aunque el token "exista".
    store.revocar_sesion("token-valido")
    assert store.obtener_sesion("token-valido") is None


def test_borrar_el_espacio_invalida_sesiones_y_filtra_recursos(store):
    """§8.5: el borrado revoca acceso en el acto; la lápida queda agendada."""

    _espacio_de_ejemplo(store)
    store.crear_sesion("token-vivo", "ws_1", horas_validez=24)

    # UNA llamada: marca borrado_en, revoca sesiones y agenda la lápida.
    store.borrar_workspace("ws_1")

    # El acceso queda bloqueado aunque la "limpieza física" sea posterior.
    assert store.obtener_workspace("ws_1") is None
    assert store.obtener_sesion("token-vivo") is None
    assert store.obtener_documento("doc_1") is None
    assert store.obtener_generacion("gen_1") is None
    assert store.listar_generaciones("ws_1") == []
    assert store.esta_borrado("workspace", "ws_1")


def test_borrar_un_documento_retira_sus_derivados_del_listado(store):
    """§8.5: "borrar un documento retira original, índice, DERIVADOS..." —
    las generaciones de un documento con lápida no aparecen en el listado
    del espacio, aunque la generación en sí no tenga lápida propia."""
    _espacio_de_ejemplo(store)  # doc_1 + gen_1 (de doc_1)
    store.registrar_documento("doc_2", "ws_1", "Otro manual", DocumentStatus.READY)
    store.registrar_generacion("gen_2", "ws_1", "doc_2", JobStatus.COMPLETED)

    # Se borra SOLO el documento 1: su derivado (gen_1) se retira del listado;
    # el del documento 2 sigue visible.
    store.agendar_borrado("document", "doc_1", "ws_1")

    ids_listados = {generacion["generation_id"] for generacion in store.listar_generaciones("ws_1")}
    assert ids_listados == {"gen_2"}
    # La generación retirada del listado tampoco se resuelve individualmente
    # si el flujo de borrado de #19 le pone lápida propia; sin ella, al menos
    # el LISTADO ya cumple §8.5 (la lápida propia la decide el orquestador).
    assert store.obtener_generacion("gen_1") is None  # la lápida del documento también protege la consulta directa


# ---------------------------------------------------------------------------
# Tombstones que sobreviven al reinicio (durables a nivel local)
# ---------------------------------------------------------------------------


def test_tombstone_sobrevive_reinicio_y_bloquea_resurreccion(ruta_db):
    """§11.5: tras retirar un recurso, ni un reinicio ni una "reconstrucción"
    desde manifiestos lo hacen reaparecer mientras la lápida esté vigente.

    Dos capas de defensa, verificadas acá:
    1. Las LECTURAS respetan la lápida: obtener_documento devuelve None.
    2. Una reconstrucción respinga consultando esta_borrado ANTES de
       insertar; si alguien inserta a lo bruto con el mismo id, SQLite lo
       rechaza (la fila original nunca se borró físicamente): no existe
       "resurrección silenciosa".
    """
    import sqlite3

    primero = RegistroOperativo(ruta_db)
    primero.crear_workspace("ws_1", "C", dias_validez=30)
    primero.registrar_documento("doc_1", "ws_1", "Manual", DocumentStatus.READY)
    primero.agendar_borrado("document", "doc_1", "ws_1")
    primero.cerrar()

    segundo = RegistroOperativo(ruta_db)
    assert segundo.esta_borrado("document", "doc_1") is True
    assert segundo.obtener_documento("doc_1") is None

    # Reconstrucción CORRECTA (la que hará el flujo de #9/#14): consulta la
    # lápida y se salta el recurso retirado.
    if not segundo.esta_borrado("document", "doc_1"):
        segundo.registrar_documento("doc_1", "ws_1", "Manual", DocumentStatus.READY)
    assert segundo.obtener_documento("doc_1") is None  # sigue retirado

    # Reconstrucción INGENUA: el mismo id choca con la fila viva (UNIQUE):
    with pytest.raises(sqlite3.IntegrityError):
        segundo.registrar_documento("doc_1", "ws_1", "Manual", DocumentStatus.READY)
    segundo.cerrar()


# ---------------------------------------------------------------------------
# Eventos: id monotónico y reconexión Last-Event-ID (§3.3)
# ---------------------------------------------------------------------------


def test_eventos_monotonicos_y_reconexion(store):
    id1 = store.registrar_evento("ws_1", "supervisor", JobStatus.RUNNING, generation_id="gen_1")
    id2 = store.registrar_evento("ws_1", "writer", JobStatus.RUNNING, generation_id="gen_1", iteration=1)
    id3 = store.registrar_evento("ws_1", "critic", JobStatus.RUNNING, generation_id="gen_1", iteration=1)

    assert id1 < id2 < id3  # monotónicos

    # El cliente se reconecta habiendo visto hasta id2: recibe solo id3.
    pendientes = store.eventos_desde("gen_1", ultimo_id=id2)
    assert [evento["id"] for evento in pendientes] == [id3]
    assert pendientes[0]["step"] == "critic"


# ---------------------------------------------------------------------------
# Migraciones versionadas
# ---------------------------------------------------------------------------


def test_migraciones_aplican_una_vez(ruta_db):
    """Abrir dos veces no re-aplica migraciones ni duplica estado."""
    primero = RegistroOperativo(ruta_db)
    assert primero.version_esquema() == 1  # VERSION_ESQUEMA actual
    _espacio_de_ejemplo(primero)
    primero.cerrar()

    segundo = RegistroOperativo(ruta_db)
    assert segundo.version_esquema() == 1
    assert segundo.obtener_workspace("ws_1") is not None  # el estado sigue
    segundo.cerrar()
