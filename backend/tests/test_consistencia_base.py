"""Regresiones reproducidas en la revisión de develop: datos, cola y contratos."""

import sqlite3
import threading
import time
from datetime import timedelta
from unittest.mock import patch

import pytest
from app.config import Configuracion
from app.core.faithfulness.faithfulness import Afirmacion, EstadoEvaluacion, Juicio, VerificadorFidelidad
from app.core.rag.chunker import trocear
from app.core.rag.parser import LimitesIngesta, ParserError, parsear_archivo
from app.jobs.manager import ControlesOperativos, CuotasModelo, CuotasProveedor, GestorTrabajos, ReintentableError
from app.jobs.store import MIGRACIONES, RegistroOperativo, _ahora
from app.main import crear_app
from app.schemas.enums import DocumentStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.pedagogical import EscenaGuion, ExecutiveSummary
from app.schemas.responses import GenerationJobResponse, PedagogicalOutput
from app.storage.oci_storage import get_storage_provider
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_schemas import salida_valida

pytestmark = pytest.mark.unit


def test_migracion_desde_v2_conserva_datos(tmp_path):
    ruta = tmp_path / "v2.db"
    con = sqlite3.connect(ruta)
    con.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, aplicada_en TEXT)")
    for version, sql in MIGRACIONES[:2]:
        con.executescript(sql)
        con.execute("INSERT INTO schema_migrations VALUES (?, '2026-01-01')", (version,))
        con.commit()
    con.execute(
        "INSERT INTO jobs(job_id,workspace_id,tipo,status,creado_en,actualizado_en) VALUES ('j','w','chat','completed','2026-01-01','2026-01-01')"
    )
    con.commit()
    con.close()
    registro = RegistroOperativo(ruta)
    try:
        assert registro.version_esquema() == 3
        fila = registro._conn.execute("SELECT * FROM jobs WHERE job_id='j'").fetchone()
        assert fila["status"] == "completed" and fila["generation_id"] is None
    finally:
        registro.cerrar()


@pytest.fixture
def registro(tmp_path):
    store = RegistroOperativo(tmp_path / "db")
    store.crear_workspace("w", "codigo", 365)
    store.registrar_documento("d", "w", "Documento", DocumentStatus.READY)
    store.registrar_generacion("g", "w", "d")
    yield store
    store.cerrar()


def esperar(store, job, estado):
    hasta = time.monotonic() + 3
    while time.monotonic() < hasta:
        fila = store.obtener_job(job)
        if fila and fila["status"] == estado:
            return fila
        time.sleep(0.01)
    pytest.fail(f"El trabajo no alcanzó {estado}: {fila}")


@pytest.mark.parametrize("tipo,recurso", [("document", "d"), ("generation", "g")])
def test_borrado_no_resucita_tras_plazo(registro, tipo, recurso):
    registro.agendar_borrado(tipo, recurso, "w")
    with patch("app.jobs.store._ahora", return_value=_ahora() + timedelta(days=31)):
        assert registro.esta_borrado(tipo, recurso)
        assert registro.obtener_generacion("g") is None
        assert registro.listar_generaciones("w") == []
        if tipo == "document":
            assert registro.obtener_documento("d") is None


def test_recursos_y_derivados_de_espacio_vencido(registro):
    with patch("app.jobs.store._ahora", return_value=_ahora() + timedelta(days=366)):
        assert registro.obtener_documento("d") is None
        assert registro.obtener_generacion("g") is None
        assert registro.listar_generaciones("w") == []
        with pytest.raises(ValueError):
            registro.registrar_generacion("otra", "w", "d")


def test_enqueue_revierte_trabajo_si_falla_evento(registro):
    gestor = GestorTrabajos(registro)
    try:
        with patch.object(registro, "registrar_evento", side_effect=sqlite3.OperationalError("locked")):
            with pytest.raises(sqlite3.OperationalError):
                gestor.enqueue("w", "generation", lambda ctx: "ok", job_id="j", generation_id="g")
        assert registro.obtener_job("j") is None
        assert registro.contar_activos_de_workspace("w") == 0
        assert registro.obtener_generacion("g")["status"] == "queued"
        assert not gestor._ejecutables
    finally:
        gestor.detener()


def test_worker_con_fallo_no_acepta_mas_trabajos(registro):
    gestor = GestorTrabajos(registro)
    try:
        with patch.object(registro, "primer_job_encolado", side_effect=sqlite3.OperationalError("locked")):
            gestor._despertar.set()
            gestor._hilo.join(timeout=2)
        assert not gestor._hilo.is_alive()
        with pytest.raises(RuntimeError, match="detenido"):
            gestor.enqueue("w", "chat", lambda ctx: "ok")
    finally:
        gestor.detener()


def test_retry_repite_solo_llamada_y_cuenta_cada_intento(registro):
    cuotas = CuotasProveedor({"modelo": CuotasModelo(rpd=10)})
    gestor = GestorTrabajos(registro, ControlesOperativos(base_backoff_segundos=0), cuotas=cuotas)
    pasos, llamadas = [], []

    def proveedor(timeout):
        llamadas.append(timeout)
        if len(llamadas) < 3:
            raise ReintentableError("temporal")
        return "ok"

    def trabajo(ctx):
        pasos.append("paso previo")
        return ctx.llamar(proveedor, modelo="modelo", tokens_estimados=10)

    try:
        job = gestor.enqueue("w", "chat", trabajo)
        assert esperar(registro, job, "completed")["intentos"] == 2
        assert len(pasos) == 1 and len(llamadas) == 3
        assert cuotas.disponibles_hoy("modelo") == 7
    finally:
        gestor.detener()


def test_error_transitorio_fuera_de_llamar_no_repite_pipeline(registro):
    gestor = GestorTrabajos(registro)
    pasos = []

    def trabajo(ctx):
        pasos.append(1)
        raise ReintentableError("No se puede repetir un pipeline con efectos ya completados")

    try:
        job = gestor.enqueue("w", "chat", trabajo)
        esperar(registro, job, "failed")
        assert len(pasos) == 1
    finally:
        gestor.detener()


def test_generacion_comparte_estado_y_eventos_con_job(registro):
    gestor = GestorTrabajos(registro)
    liberar = threading.Event()
    try:
        job = gestor.enqueue("w", "generation", lambda ctx: liberar.wait(2) or "ok", generation_id="g")
        esperar(registro, job, "running")
        assert registro.obtener_generacion("g")["status"] == "running"
        liberar.set()
        esperar(registro, job, "completed")
        assert registro.obtener_generacion("g")["status"] == "completed"
        eventos = registro.eventos_desde_job(job)
        assert [e["status"] for e in eventos] == ["queued", "running", "completed"]
        assert registro.eventos_desde("g") == eventos
        assert registro.eventos_desde_job(job, eventos[0]["id"]) == eventos[1:]
    finally:
        liberar.set()
        gestor.detener()


def test_generacion_borrada_durante_ejecucion_no_publica(registro):
    gestor = GestorTrabajos(registro)
    liberar = threading.Event()
    try:
        job = gestor.enqueue("w", "generation", lambda ctx: liberar.wait(2) or "ok", generation_id="g")
        esperar(registro, job, "running")
        registro.agendar_borrado("document", "d", "w")
        liberar.set()
        assert esperar(registro, job, "cancelled")["resultado"] is None
    finally:
        liberar.set()
        gestor.detener()


def test_reinicio_sincroniza_generacion_vinculada(registro):
    registro.encolar_job("j", "w", "generation", generation_id="g")
    gestor = GestorTrabajos(registro)
    try:
        assert registro.obtener_job("j")["status"] == "failed"
        assert registro.obtener_generacion("g")["error_code"] == "INTERRUPTED"
        assert registro.eventos_desde_job("j")[-1]["status"] == "failed"
    finally:
        gestor.detener()


def test_aislamiento_de_vinculo_generacion(registro):
    registro.crear_workspace("otro", "otro", 30)
    with pytest.raises(ValueError):
        registro.encolar_job("j", "otro", "generation", generation_id="g")
    assert registro.obtener_job("j") is None


def test_parser_limita_tokens_reales_y_normaliza_salto_pagina():
    with pytest.raises(ParserError) as error:
        parsear_archivo(("🙂a " * 100).encode(), "unicode.txt", LimitesIngesta(max_tokens_extraidos=150))
    assert error.value.codigo.value == "LIMITE_TOKENS"
    resultado = parsear_archivo(b"uno\x0cdos", "pagina.txt")
    assert resultado.secciones[0].texto == "uno\ndos"
    assert trocear(resultado, "w", "d")[0].linea_fin == 2


def test_pdf_demo_solo_diagrama_requiere_vision(documento_demo_vcn):
    resultado = parsear_archivo(documento_demo_vcn.read_bytes(), documento_demo_vcn.name)
    assert resultado.paginas_visuales == [3]
    assert resultado.estado_documento == DocumentStatus.PROCESSING


def test_factory_usa_configuracion_del_archivo(tmp_path, monkeypatch):
    monkeypatch.delenv("MOCK_OCI", raising=False)
    archivo = tmp_path / "config.env"
    archivo.write_text(f"MOCK_OCI=true\nDATA_DIR={tmp_path.as_posix()}\n")
    ajustes = Configuracion(_env_file=archivo)
    proveedor = get_storage_provider(configuracion=ajustes)
    proveedor.upload("test/objeto", "contenido")
    assert proveedor.get_as_text("test/objeto") == "contenido"
    assert proveedor.base_dir == (tmp_path / "oci_mock_storage").resolve()


def test_exclusiones_ambiguas_exigen_rangos_exactos():
    textos = []

    def descomponer(texto):
        textos.append(texto)
        return [Afirmacion("a", texto)]

    verificador = VerificadorFidelidad(descomponer, lambda a, c: [Juicio(x.id, False) for x in a])
    texto = "Justificación: Una VCN no es pública.\nOpción B: pública"
    assert verificador.verificar(texto, "Fuente", distractores=["pública"]).estado == EstadoEvaluacion.NO_EVALUABLE
    assert not textos
    inicio = texto.index("Opción B:")
    verificador.verificar(texto, "Fuente", distractores=["pública"], rangos_excluidos=[(inicio, len(texto))])
    assert "Una VCN no es pública." in textos[0] and "Opción B:" not in textos[0]


@pytest.mark.parametrize("rangos", [[(1, 100)], [(2, 1)], [(0, 3), (2, 4)], [(None, 2)]])
def test_rangos_invalidos_no_consumen_juez(rangos):
    def no_llamar(*args):
        pytest.fail("No se debe consumir el proveedor con exclusiones inválidas")

    resultado = VerificadorFidelidad(no_llamar, no_llamar).verificar("texto", "fuente", rangos_excluidos=rangos)
    assert resultado.estado == EstadoEvaluacion.NO_EVALUABLE


def test_esquemas_no_aceptan_textos_vacios_ni_infinito():
    with pytest.raises(ValidationError):
        ExecutiveSummary(
            tipo="resumen_ejecutivo",
            titulo="Título",
            puntos_clave=[""],
            impacto_cualitativo="Impacto",
            implicaciones=["Texto"],
            acciones=["Acción"],
            referencias=[{"chunk_id": "c"}],
        )
    with pytest.raises(ValidationError):
        EscenaGuion(
            id="e",
            duracion_min="Infinity",
            narracion="Texto",
            puntos_diapositiva=["Punto"],
            referencias=[{"chunk_id": "c"}],
        )
    with pytest.raises(ValidationError):
        ExecutiveSummary(
            tipo="resumen_ejecutivo",
            titulo=" ",
            puntos_clave=["Punto"],
            impacto_cualitativo="Impacto",
            implicaciones=["Texto"],
            acciones=["Acción"],
            referencias=[{"chunk_id": "c"}],
        )


def test_respuesta_publica_quiz_no_filtra_soluciones():
    quiz = {
        "tipo": "quiz",
        "titulo": "Título",
        "preguntas": [
            {
                "id": "p",
                "enunciado": "Pregunta",
                "opciones": [{"option_id": o, "texto": o} for o in "ABCD"],
                "correct_option_id": "A",
                "justificacion": "La solución",
                "referencias": [{"chunk_id": "c"}],
            }
        ],
    }
    paquete = salida_valida(quiz)
    respuesta = GenerationJobResponse(
        generation_id=paquete.generation_id,
        status="completed",
        status_url="/s",
        events_url="/e",
        contenido=paquete,
        persistencia={"status_upload": "completado", "provider": "mock"},
    )
    publico = respuesta.model_dump_json()
    assert "correct_option_id" not in publico and "justificacion" not in publico
    assert GenerationJobResponse.model_validate_json(publico) == respuesta
    assert "correct_option_id" in paquete.model_dump_json()
    with pytest.raises(ValidationError):
        PedagogicalOutput.model_validate(respuesta.contenido)


@pytest.mark.parametrize(
    "codigo,status",
    [
        (ErrorCode.QUEUE_FULL, 429),
        (ErrorCode.IDEMPOTENCY_CONFLICT, 409),
        (ErrorCode.PROVIDER_UNAVAILABLE, 503),
        (ErrorCode.RECOVERY_LOCKED, 429),
    ],
)
def test_error_dominio_conserva_codigo_y_request_id(codigo, status):
    app = crear_app(Configuracion(app_env="test"))

    @app.get("/prueba")
    def falla():
        raise ErrorAplicacion(codigo, "Mensaje público", {"dato": 1})

    with TestClient(app) as cliente:
        respuesta = cliente.get("/prueba")
    assert respuesta.status_code == status
    assert respuesta.json()["error"]["code"] == codigo.value
    assert respuesta.json()["request_id"] == respuesta.headers["X-Request-ID"]
