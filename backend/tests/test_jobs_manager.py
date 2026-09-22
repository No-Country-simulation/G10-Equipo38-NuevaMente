"""Tests del gestor de trabajos (issue #20).

Verificación exacta del issue: `pytest backend/tests/test_jobs_manager.py`
con trabajos sintéticos configurables (duración, fallo, cancelación).

Criterio por criterio:

1. Dos generaciones simultáneas desde espacios distintos: una corre y la
   otra queda en cola con posición consultable.
2. Con un trabajo activo y cinco en espera, una solicitud adicional →
   rechazo de cola llena (la API lo traduce a 429 QUEUE_FULL).
3. Cancelar en cola la saca sin ejecutarla; cancelar en ejecución termina
   el trabajo sin publicar contenido.
4. Un trabajo que excede su deadline de ejecución termina failed/DEADLINE.
5. Mientras un trabajo corre, la instancia sigue respondiendo consultas
   (el worker es un hilo dedicado; la API no se congela, §14.2).

Además: 1 trabajo por espacio, expiración en cola, reintentos con backoff
para errores transitorios, cuota diaria por modelo (RPM/RPD), eventos
persistidos y recuperación de huérfanos al reiniciar.

Cómo leer los helpers: `esperar_estado` sondea el store hasta que el
trabajo llegue al estado esperado (el worker corre en OTRO hilo; los
tests no duermen a ciegas, esperan con plazo). Los controles operativos
se inyectan con valores pequeños para que cada caso tarde milisegundos.
"""

import threading
import time

import pytest
from app.jobs.manager import (
    ColaLlenaError,
    ContextoEjecucion,
    ControlesOperativos,
    CuotaAgotadaError,
    CuotasModelo,
    CuotasProveedor,
    EspacioOcupadoError,
    GestorTrabajos,
    ReintentableError,
)
from app.jobs.store import RegistroOperativo
from app.schemas.enums import JobStatus

pytestmark = pytest.mark.unit


def test_cancelar_durante_llamada_sin_chequeos_no_publica(gestor, store):
    liberar = threading.Event()
    job = gestor.enqueue("ws_a", "chat", lambda ctx: liberar.wait(2) or "resultado")
    esperarlo(store, job, JobStatus.RUNNING)
    gestor.cancelar(job)
    liberar.set()
    assert esperarlo(store, job, JobStatus.CANCELLED)["resultado"] is None


def test_deadline_se_valida_al_retornar_sin_chequeos(store):
    gestor = GestorTrabajos(store, ControlesOperativos(deadline_ejecucion_segundos=0.01))
    try:
        job = gestor.enqueue("ws_a", "chat", lambda ctx: time.sleep(0.03) or "tardío")
        assert esperarlo(store, job, JobStatus.FAILED)["error_code"] == "DEADLINE"
    finally:
        gestor.detener()


def test_posiciones_fifo_distintas_y_evento_de_cancelacion(gestor, store):
    liberar = threading.Event()
    job = gestor.enqueue("ws_a", "chat", lambda ctx: liberar.wait(2))
    esperarlo(store, job, JobStatus.RUNNING)
    store.crear_workspace("ws_c", "C", 30)
    segundo = gestor.enqueue("ws_b", "chat", lambda ctx: "ok")
    tercero = gestor.enqueue("ws_c", "chat", lambda ctx: "ok")
    assert gestor.estado(segundo)["posicion_cola"] == 1
    assert gestor.estado(tercero)["posicion_cola"] == 2
    gestor.cancelar(segundo)
    assert (
        store._conn.execute("SELECT status FROM events WHERE job_id=? ORDER BY id DESC", (segundo,)).fetchone()[0]
        == "cancelled"
    )
    liberar.set()


def test_reinicio_recupera_running(store):
    store.registrar_job("interrumpido", "ws_a", "chat", JobStatus.RUNNING)
    gestor = GestorTrabajos(store)
    try:
        assert store.obtener_job("interrumpido")["error_code"] == "INTERRUPTED"
    finally:
        gestor.detener()


def test_segundo_gestor_no_roba_trabajos_del_primero(gestor, store):
    with pytest.raises(RuntimeError, match="Ya existe"):
        GestorTrabajos(store)


def test_rechazo_de_calidad_no_publica_ni_se_confunde_con_fallo(gestor, store):
    from app.jobs.manager import RechazoCalidadError

    def rechazar(ctx):
        raise RechazoCalidadError()

    job = gestor.enqueue("ws_a", "generacion", rechazar)
    assert esperarlo(store, job, JobStatus.REJECTED_QUALITY)["resultado"] is None


def test_cola_se_drena_sin_espera_artificial(gestor, store):
    liberar = threading.Event()
    primero = gestor.enqueue("ws_a", "chat", lambda ctx: liberar.wait(2))
    esperarlo(store, primero, JobStatus.RUNNING)
    segundo = gestor.enqueue("ws_b", "chat", lambda ctx: "ok")
    liberar.set()
    esperarlo(store, segundo, JobStatus.COMPLETED, plazo=0.5)


def test_rechazos_de_cuota_no_consumen_presupuesto():
    cuotas = CuotasProveedor({"m": CuotasModelo(rpm=1, tpm=10, rpd=2)})
    with pytest.raises(CuotaAgotadaError):
        cuotas.permitir("m", 11)
    cuotas.permitir("m", 5)
    assert cuotas.disponibles_hoy("m") == 1
    with pytest.raises(ValueError):
        cuotas.permitir("m", -1)
    with pytest.raises(CuotaAgotadaError):
        cuotas.permitir("sin-configurar")


def test_retry_after_no_se_recorta(store):
    gestor = GestorTrabajos(
        store, ControlesOperativos(deadline_ejecucion_segundos=0.1), cuotas=CuotasProveedor({"test": CuotasModelo()})
    )
    intentos = []
    try:

        def trabajo(ctx):
            intentos.append(1)
            raise ReintentableError("esperar", retry_after=10)

        job = gestor.enqueue("ws_a", "chat", lambda ctx: ctx.llamar(lambda timeout: trabajo(ctx), modelo="test"))
        assert esperarlo(store, job, JobStatus.FAILED)["error_code"] == "DEADLINE"
        assert len(intentos) == 1
    finally:
        gestor.detener()


def test_llamadas_reciben_timeout_y_reservan_cuota():
    cuotas = CuotasProveedor({"m": CuotasModelo(rpd=1)})
    ctx = ContextoEjecucion("job", "ws", "chat", time.monotonic() + 30, timeout_por_llamada=0.2, cuotas=cuotas)
    assert ctx.llamar(lambda *, timeout: timeout, modelo="m") == 0.2
    with pytest.raises(CuotaAgotadaError):
        ctx.llamar(lambda *, timeout: "no llega", modelo="m")


def test_borrado_durante_llamada_descarta_resultado(gestor, store):
    liberar = threading.Event()
    job = gestor.enqueue("ws_a", "chat", lambda ctx: liberar.wait(2) or "privado")
    esperarlo(store, job, JobStatus.RUNNING)
    store.borrar_workspace("ws_a")
    liberar.set()
    gestor.detener()
    fila = store._conn.execute("SELECT status, resultado FROM jobs WHERE job_id=?", (job,)).fetchone()
    assert fila["status"] == "cancelled" and fila["resultado"] is None


# Controles acelerados: mismos CAMINOS de código que producción (300 s),
# con plazos que hacen cada test casi instantáneo.
CONTROLES_RAPIDOS = ControlesOperativos(
    max_en_cola=5,
    deadline_ejecucion_segundos=1.0,
    espera_maxima_en_cola_segundos=0.6,
    timeout_por_llamada_segundos=0.5,
    reintentos_transitorios=2,
    base_backoff_segundos=0.01,
)


@pytest.fixture
def store(tmp_path) -> RegistroOperativo:
    registro = RegistroOperativo(tmp_path / "gestor.sqlite3")
    registro.crear_workspace("ws_a", "CODIGO-A", dias_validez=30)
    registro.crear_workspace("ws_b", "CODIGO-B", dias_validez=30)
    yield registro
    registro.cerrar()


@pytest.fixture
def gestor(store) -> GestorTrabajos:
    gesto = GestorTrabajos(
        store, controles=CONTROLES_RAPIDOS, cuotas=CuotasProveedor({"test": CuotasModelo(rpm=100, rpd=100)})
    )
    yield gesto
    gesto.detener()


def esperarlo(store: RegistroOperativo, job_id: str, estado: JobStatus, plazo: float = 3.0) -> dict:
    """Sondea el store hasta que el trabajo llegue a `estado` (o corte el plazo)."""
    inicio = time.monotonic()
    while time.monotonic() - inicio < plazo:
        trabajo = store.obtener_job(job_id)
        if trabajo and trabajo["status"] == estado.value:
            return trabajo
        time.sleep(0.01)
    trabajo = store.obtener_job(job_id)
    pytest.fail(f"el trabajo {job_id} no llegó a {estado.value}: quedó en {trabajo and trabajo['status']}")


# ---------------------------------------------------------------------------
# Criterio 1: una corre, la otra espera con posición visible
# ---------------------------------------------------------------------------


def test_dos_trabajos_de_espacios_distintos_uno_corre_y_otro_espera(gestor, store):
    """§7.5: ranura global 1. El segundo trabajo entra en cola y su
    posición es consultable mientras espera."""
    liberar = threading.Event()

    def trabajo_largo(contexto: ContextoEjecucion) -> str:
        liberar.wait(timeout=3)  # ocupa la ranura hasta que el test lo libere
        return "listo"

    primero = gestor.enqueue("ws_a", "generacion", trabajo_largo)
    esperarlo(store, primero, JobStatus.RUNNING)

    segundo = gestor.enqueue("ws_b", "generacion", lambda contexto: "segundo")
    esperarlo(store, segundo, JobStatus.QUEUED)
    estado = gestor.estado(segundo)
    assert estado["status"] == JobStatus.QUEUED.value
    assert estado["posicion_cola"] >= 1  # posición visible (criterio)

    liberar.set()
    esperarlo(store, primero, JobStatus.COMPLETED)
    esperarlo(store, segundo, JobStatus.COMPLETED)  # FIFO: entra cuando se libera


# ---------------------------------------------------------------------------
# Criterio 2: cola llena → rechazo claro (429 en la capa API)
# ---------------------------------------------------------------------------


def test_cola_llena_rechaza_la_sexta_solicitud(gestor, store):
    """Con la ranura ocupada y 5 en espera, la 6ª petición se rechaza con
    el mensaje accionable que la API traduce a 429 QUEUE_FULL."""
    liberar = threading.Event()

    def ocupar_ranura(contexto: ContextoEjecucion) -> str:
        liberar.wait(timeout=5)
        return "listo"

    activo = gestor.enqueue("ws_a", "generacion", ocupar_ranura)
    esperarlo(store, activo, JobStatus.RUNNING)

    # Llenar la cola con 5 trabajos de OTROS espacios (1 por espacio: hace
    # falta un workspace distinto por cada uno, otra regla de §7.5).
    for indice in range(5):
        store.crear_workspace(f"ws_{indice}", f"CODIGO-{indice}", dias_validez=30)
        gestor.enqueue(f"ws_{indice}", "ingestion", lambda contexto: "ok")
    assert store.contar_trabajos_en_cola() == 5

    store.crear_workspace("ws_extra", "CODIGO-EXTRA", dias_validez=30)
    with pytest.raises(ColaLlenaError) as info:
        gestor.enqueue("ws_extra", "generacion", lambda contexto: "nunca corre")
    assert "5" in str(info.value) and "cola" in str(info.value).lower()

    liberar.set()
    esperarlo(store, activo, JobStatus.COMPLETED)


def test_un_trabajo_por_espacio(gestor, store):
    """§7.5: un espacio no acumula trabajos activos."""
    gestor.enqueue("ws_a", "generacion", lambda contexto: "ok")
    with pytest.raises(EspacioOcupadoError):
        gestor.enqueue("ws_a", "ingestion", lambda contexto: "nunca")


# ---------------------------------------------------------------------------
# Criterio 3: cancelación (en cola y en ejecución)
# ---------------------------------------------------------------------------


def test_cancelar_en_cola_la_saca_sin_ejecutarla(gestor, store):
    ejecutado = {"segundo": False}

    liberar = threading.Event()

    def primero_trabajo(contexto: ContextoEjecucion) -> str:
        liberar.wait(timeout=3)
        return "listo"

    def segundo_trabajo(contexto: ContextoEjecucion) -> str:
        ejecutado["segundo"] = True  # NO debe correr: se cancela en cola
        return "no debería"

    gestor.enqueue("ws_a", "generacion", primero_trabajo)
    segundo = gestor.enqueue("ws_b", "generacion", segundo_trabajo)
    esperarlo(store, segundo, JobStatus.QUEUED)

    assert gestor.cancelar(segundo) is True
    esperarlo(store, segundo, JobStatus.CANCELLED)
    liberar.set()
    time.sleep(0.05)
    assert ejecutado["segundo"] is False  # jamás se ejecutó (criterio)


def test_cancelar_en_ejecucion_termina_sin_publicar_contenido(gestor, store):
    """El flag se prende; la función lo ve entre pasos y aborta. El
    resultado NUNCA se persiste (sin contenido publicado)."""
    publico = {"publicado": False}
    dentro = threading.Event()

    def trabajo(contexto: ContextoEjecucion) -> str:
        dentro.set()
        for _ in range(200):  # "pasos" del trabajo, chequeando el flag
            contexto.chequear()
            time.sleep(0.01)
        publico["publicado"] = True  # esto NO debe pasar
        return "resultado que no debe existir"

    job_id = gestor.enqueue("ws_a", "generacion", trabajo)
    esperarlo(store, job_id, JobStatus.RUNNING)
    dentro.wait(timeout=2)
    gestor.cancelar(job_id)

    final = esperarlo(store, job_id, JobStatus.CANCELLED)
    assert final["resultado"] is None  # no publicó contenido
    assert publico["publicado"] is False


# ---------------------------------------------------------------------------
# Criterio 4: deadline de ejecución
# ---------------------------------------------------------------------------


def test_trabajo_que_excede_el_deadline_termina_failed_deadline(gestor, store):
    def trabajo_lento(contexto: ContextoEjecucion) -> str:
        for _ in range(300):  # 3 s > deadline de 1 s de los controles rápidos
            contexto.chequear()  # el chequeo cooperativo detecta el deadline
            time.sleep(0.01)
        return "nunca"

    job_id = gestor.enqueue("ws_a", "generacion", trabajo_lento)
    final = esperarlo(store, job_id, JobStatus.FAILED)
    assert final["error_code"] == "DEADLINE"


def test_expiracion_en_cola_si_nadie_lo_levanta(gestor, store):
    """§7.5: espera máxima en cola 300 s (0.6 s acá). El primero ocupa la
    ranura para siempre; el segundo expira en cola sin ejecutarse."""
    liberar = threading.Event()

    gestor.enqueue("ws_a", "generacion", lambda contexto: liberar.wait(timeout=3) or "listo")
    segundo = gestor.enqueue("ws_b", "generacion", lambda contexto: "nunca")
    time.sleep(0.1)

    final = esperarlo(store, segundo, JobStatus.FAILED, plazo=5)
    assert final["error_code"] == "COLA_EXPIRADA"
    liberar.set()


# ---------------------------------------------------------------------------
# Criterio 5: el gestor no congela a quien consulta
# ---------------------------------------------------------------------------


def test_las_consultas_responden_mientras_corre_un_trabajo(gestor, store):
    """§14.2: API y SSE disponibles durante trabajo intensivo. Aquí: las
    consultas de estado retornan al instante mientras el worker ejecuta."""
    liberar = threading.Event()

    job_id = gestor.enqueue("ws_a", "generacion", lambda contexto: liberar.wait(timeout=3) or "listo")
    esperarlo(store, job_id, JobStatus.RUNNING)

    inicio = time.monotonic()
    estado = gestor.estado(job_id)
    demora = time.monotonic() - inicio

    assert estado["status"] == JobStatus.RUNNING.value
    assert demora < 0.2  # responde mientras el trabajo corre
    liberar.set()
    esperarlo(store, job_id, JobStatus.COMPLETED)


# ---------------------------------------------------------------------------
# Reintentos, cuotas y recuperación
# ---------------------------------------------------------------------------


def test_error_transitorio_reintenta_y_completa(gestor, store):
    """§7.5: hasta 2 reintentos con backoff para transitorios. El trabajo
    falla UNA vez y a la segunda completa."""
    intentos = {"n": 0}

    def inestable(contexto: ContextoEjecucion) -> str:
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise ReintentableError("503 del proveedor")
        return "recuperado"

    job_id = gestor.enqueue("ws_a", "chat", lambda ctx: ctx.llamar(lambda timeout: inestable(ctx), modelo="test"))
    final = esperarlo(store, job_id, JobStatus.COMPLETED)
    assert intentos["n"] == 2
    assert final["resultado"] == '"recuperado"'


def test_reintentos_agotados_falla_con_codigo(gestor, store):
    def siempre_caida(contexto: ContextoEjecucion) -> str:
        raise ReintentableError("timeout de red")

    job_id = gestor.enqueue("ws_a", "chat", lambda ctx: ctx.llamar(lambda timeout: siempre_caida(ctx), modelo="test"))
    final = esperarlo(store, job_id, JobStatus.FAILED, plazo=5)
    assert final["error_code"] == "REINTENTOS_AGOTADOS"
    # La columna cuenta REINTENTOS efectuados (§7.5: hasta dos), no el
    # intento original: 2 reintentos => 3 ejecuciones de la función.
    assert final["intentos"] == 2


def test_error_permanente_no_reintenta(gestor, store):
    intentos = {"n": 0}

    def roto(contexto: ContextoEjecucion) -> str:
        intentos["n"] += 1
        raise ValueError("bug del trabajo")

    job_id = gestor.enqueue("ws_a", "glosario", roto)
    final = esperarlo(store, job_id, JobStatus.FAILED)
    assert final["error_code"] == "ERROR_TRABAJO"
    assert intentos["n"] == 1  # no se quemó presupuesto en reintentos


def test_cuota_diaria_agotada_detiene_el_trabajo(store, tmp_path):
    """§7.5: RPD agotado → failed/CUOTA_AGOTADA (falla técnica, sin insistir)."""
    cuotas = CuotasProveedor({"gemini-2.5-flash": CuotasModelo(rpm=100, tpm=10**9, rpd=1)})
    registro = store
    gesto = GestorTrabajos(registro, controles=CONTROLES_RAPIDOS, cuotas=cuotas)
    try:

        def gastador(contexto: ContextoEjecucion) -> str:
            gesto.cuotas.permitir("gemini-2.5-flash")  # 1ª llamada: ok
            gesto.cuotas.permitir("gemini-2.5-flash")  # 2ª: RPD=1 agotado
            return "no llega"

        job_id = gesto.enqueue("ws_a", "generacion", gastador)
        final = esperarlo(registro, job_id, JobStatus.FAILED)
        assert final["error_code"] == "CUOTA_AGOTADA"
    finally:
        gesto.detener()


def test_cuotas_rpm_cuentan_reintentos():
    """§7.5: los reintentos consumen ventana: 3 llamadas en el minuto con
    RPM=2 → la tercera se bloquea."""
    cuotas = CuotasProveedor({"m": CuotasModelo(rpm=2, tpm=10**9, rpd=100)})
    cuotas.permitir("m")
    cuotas.permitir("m")
    with pytest.raises(CuotaAgotadaError, match="RPM"):
        cuotas.permitir("m")


def test_eventos_persistidos_con_id_monotonico(gestor, store):
    """Los eventos del ciclo de vida alimentan el SSE de #31 (Last-Event-ID)."""
    job_id = gestor.enqueue("ws_a", "ingestion", lambda contexto: "ok")
    esperarlo(store, job_id, JobStatus.COMPLETED)

    eventos = store._conn.execute("SELECT * FROM events WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
    pasos = [evento["status"] for evento in eventos]
    assert pasos == ["queued", "running", "completed"]
    ids = [evento["id"] for evento in eventos]
    assert ids == sorted(ids)  # monotónicos


def test_huerfanos_en_cola_se_marcan_al_arrancar(store):
    """Reinicio: un queued que sobrevivió a otro proceso queda failed (la
    función ejecutable murió con él; el usuario reintenta con idempotencia)."""
    store.registrar_job("job_huerfano", "ws_a", "generacion")

    gestor = GestorTrabajos(
        store, controles=CONTROLES_RAPIDOS, cuotas=CuotasProveedor({"test": CuotasModelo(rpm=100, rpd=100)})
    )
    try:
        huerfano = store.obtener_job("job_huerfano")
        assert huerfano["status"] == JobStatus.FAILED.value
        assert huerfano["error_code"] == "INTERRUPTED"
    finally:
        gestor.detener()
