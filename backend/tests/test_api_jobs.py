"""Contratos HTTP del issue #20: aislamiento, cancelación y reconexión SSE."""

import json
import threading
import time

import pytest
from app.api.routes import jobs
from app.api.security import _peticiones_globales, _peticiones_ip
from app.config import Configuracion
from app.main import crear_app
from app.schemas.enums import JobStatus
from app.session.manager import SessionManager
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration_mock


@pytest.fixture
def entorno(tmp_path):
    _peticiones_globales.clear()
    _peticiones_ip.clear()
    app = crear_app(Configuracion(app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path)))
    with TestClient(app) as cliente:
        espacio = cliente.post("/api/workspaces").json()
        cabeceras = {"Authorization": f"Bearer {espacio['token']}"}
        yield cliente, app, espacio, cabeceras


def esperar(gestor, job_id, estado):
    limite = time.monotonic() + 3
    while time.monotonic() < limite:
        trabajo = gestor.estado(job_id)
        if trabajo["status"] == estado.value:
            return trabajo
        time.sleep(0.01)
    pytest.fail(f"El trabajo no llegó a {estado.value}")


def completar(entorno, resultado=None):
    _, app, espacio, _ = entorno
    gestor = app.state.jobs_manager
    job_id = gestor.enqueue(espacio["workspace_id"], "chat", lambda ctx: resultado or {"respuesta": "VCN"})
    esperar(gestor, job_id, JobStatus.COMPLETED)
    return job_id


@pytest.mark.parametrize("ruta,metodo", [("", "get"), ("/events", "get"), ("/cancel", "post")])
def test_autenticacion_y_ownership(entorno, ruta, metodo):
    cliente, app, _, cabeceras = entorno
    job_id = completar(entorno)
    url = f"/api/jobs/{job_id}{ruta}"
    assert getattr(cliente, metodo)(url).status_code == 401
    otro = cliente.post("/api/workspaces").json()
    ajenas = {"Authorization": f"Bearer {otro['token']}"}
    respuesta = getattr(cliente, metodo)(url, headers=ajenas)
    assert respuesta.status_code == 404
    assert respuesta.json()["error"]["code"] == "NOT_FOUND"
    assert getattr(cliente, metodo)(f"/api/jobs/inexistente{ruta}", headers=cabeceras).status_code == 404
    assert app.state.jobs_manager.estado(job_id)["status"] == "completed"


def test_estado_y_resultado_publico(entorno):
    cliente, _, _, cabeceras = entorno
    job_id = completar(entorno)
    respuesta = cliente.get(f"/api/jobs/{job_id}", headers=cabeceras)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "completed" and cuerpo["result"] == {"respuesta": "VCN"}
    assert cuerpo["cancel_url"] is None
    assert cuerpo["events_url"] == f"/api/jobs/{job_id}/events"
    assert "workspace_id" not in cuerpo and "resultado" not in cuerpo
    assert "X-Request-ID" in respuesta.headers
    assert cliente.post(f"/api/jobs/{job_id}/cancel", headers=cabeceras).status_code == 409


@pytest.mark.parametrize("estado", [JobStatus.FAILED, JobStatus.REJECTED_QUALITY, JobStatus.CANCELLED])
def test_no_expone_borradores_terminales(entorno, estado):
    cliente, app, espacio, cabeceras = entorno
    app.state.db.registrar_job("terminal", espacio["workspace_id"], "chat", estado)
    app.state.db.actualizar_job("terminal", estado, resultado=json.dumps({"borrador": "privado"}))
    respuesta = cliente.get("/api/jobs/terminal", headers=cabeceras)
    assert respuesta.json()["result"] is None
    assert respuesta.json()["error"]["code"] == estado.value.upper()
    assert "privado" not in respuesta.text


def test_trabajos_generacion_no_exponen_canonico_por_jobs(entorno):
    cliente, app, espacio, cabeceras = entorno
    app.state.db.registrar_job("generacion", espacio["workspace_id"], "generacion", JobStatus.COMPLETED)
    assert cliente.get("/api/jobs/generacion", headers=cabeceras).status_code == 404


def test_cancelacion_en_cola_es_idempotente(entorno):
    cliente, app, espacio, cabeceras = entorno
    otro = cliente.post("/api/workspaces").json()
    liberar = threading.Event()
    gestor = app.state.jobs_manager
    try:
        activo = gestor.enqueue(otro["workspace_id"], "chat", lambda ctx: liberar.wait(3))
        esperar(gestor, activo, JobStatus.RUNNING)
        en_cola = gestor.enqueue(espacio["workspace_id"], "ingestion", lambda ctx: {"document_id": "doc"})
        estado = cliente.get(f"/api/jobs/{en_cola}", headers=cabeceras).json()
        assert estado["posicion_cola"] == 1 and estado["result"] is None
        for _ in range(2):
            respuesta = cliente.post(f"/api/jobs/{en_cola}/cancel", headers=cabeceras)
            assert respuesta.status_code == 200 and respuesta.json()["status"] == "cancelled"
    finally:
        liberar.set()


def test_cancelacion_en_ejecucion_no_publica_resultado(entorno):
    cliente, app, espacio, cabeceras = entorno
    liberar = threading.Event()
    gestor = app.state.jobs_manager

    def trabajo(ctx):
        liberar.wait(3)
        return {"respuesta": "no debe publicarse"}

    try:
        job_id = gestor.enqueue(espacio["workspace_id"], "chat", trabajo)
        esperar(gestor, job_id, JobStatus.RUNNING)
        respuesta = cliente.post(f"/api/jobs/{job_id}/cancel", headers=cabeceras)
        assert respuesta.status_code == 200 and respuesta.json()["status"] == "running"
        assert cliente.get("/api/health").status_code == 200
        liberar.set()
        esperar(gestor, job_id, JobStatus.CANCELLED)
        assert cliente.get(f"/api/jobs/{job_id}", headers=cabeceras).json()["result"] is None
    finally:
        liberar.set()


def test_sse_cursor_y_encuadre_sin_resultados(entorno):
    cliente, app, _, cabeceras = entorno
    job_id = completar(entorno)
    eventos = app.state.db.eventos_desde_job(job_id)
    respuesta = cliente.get(f"/api/jobs/{job_id}/events", headers=cabeceras)
    assert respuesta.headers["content-type"].startswith("text/event-stream")
    assert respuesta.headers["x-accel-buffering"] == "no"
    frames = respuesta.text.rstrip().split("\n\n")
    assert len(frames) == len(eventos)
    assert respuesta.text.endswith("\n\n")
    assert "respuesta" not in respuesta.text
    assert (
        cliente.get(
            f"/api/jobs/{job_id}/events",
            headers={**cabeceras, "Last-Event-ID": str(eventos[-1]["id"])},
        ).text
        == ""
    )
    ultimo = eventos[-2]["id"]
    reconexion = cliente.get(f"/api/jobs/{job_id}/events", headers={**cabeceras, "Last-Event-ID": str(ultimo)})
    assert reconexion.text.count("event: progress") == 1
    assert f"id: {eventos[-1]['id']}\n" in reconexion.text
    assert app.state.db.eventos_desde_job(job_id) == eventos


@pytest.mark.parametrize("cursor", ["texto", "-1", str(2**63)])
def test_sse_rechaza_cursor_invalido(entorno, cursor):
    cliente, _, _, cabeceras = entorno
    job_id = completar(entorno)
    respuesta = cliente.get(f"/api/jobs/{job_id}/events", headers={**cabeceras, "Last-Event-ID": cursor})
    assert respuesta.status_code == 422 and respuesta.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_heartbeat_y_revocacion_durante_stream(entorno, monkeypatch):
    _, app, espacio, _ = entorno
    gestor = app.state.jobs_manager
    liberar = threading.Event()
    job_id = gestor.enqueue(espacio["workspace_id"], "chat", lambda ctx: liberar.wait(3))
    esperar(gestor, job_id, JobStatus.RUNNING)
    ultimo = app.state.db.eventos_desde_job(job_id)[-1]["id"]
    monkeypatch.setattr(jobs, "INTERVALO_HEARTBEAT", 0)
    monkeypatch.setattr(jobs, "INTERVALO_SONDEO", 0)
    manager = SessionManager(
        app.state.db,
        app.state.storage_provider,
        app.state.config.workspace_retention_days,
        app.state.config.session_max_hours,
    )

    class Peticion:
        async def is_disconnected(self):
            return False

    stream = jobs._stream(Peticion(), gestor, manager, espacio, job_id, ultimo)
    try:
        assert await anext(stream) == ": heartbeat\n\n"
        manager.revocar_sesion(espacio["token"])
        error = await anext(stream)
        assert "event: error" in error and "SESSION_INVALID" in error
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        # La conexión no canceló el trabajo.
        assert gestor.estado(job_id)["status"] == "running"
    finally:
        await stream.aclose()
        liberar.set()


@pytest.mark.asyncio
async def test_desconexion_no_cancela_el_trabajo(entorno):
    _, app, espacio, _ = entorno
    gestor = app.state.jobs_manager
    liberar = threading.Event()
    job_id = gestor.enqueue(espacio["workspace_id"], "chat", lambda ctx: liberar.wait(3))
    esperar(gestor, job_id, JobStatus.RUNNING)
    manager = SessionManager(
        app.state.db,
        app.state.storage_provider,
        app.state.config.workspace_retention_days,
        app.state.config.session_max_hours,
    )

    class Desconectada:
        async def is_disconnected(self):
            return True

    stream = jobs._stream(Desconectada(), gestor, manager, espacio, job_id, 0)
    try:
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert gestor.estado(job_id)["status"] == "running"
    finally:
        await stream.aclose()
        liberar.set()
