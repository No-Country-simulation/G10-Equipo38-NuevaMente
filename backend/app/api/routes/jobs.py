"""Consulta, SSE y cancelación de trabajos comunes (issue #20)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_sesion_actual, get_session_manager
from app.jobs.manager import GestorTrabajos
from app.schemas.enums import JobStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.internal import TrabajoComunResponse
from app.session.manager import SessionManager

router = APIRouter(prefix="/api/jobs", tags=["Trabajos"])
INTERVALO_SONDEO = 0.25
INTERVALO_HEARTBEAT = 15.0
ACTIVOS = {JobStatus.QUEUED.value, JobStatus.RUNNING.value}
TIPOS_COMUNES = {"ingestion", "chat", "glosario"}


def get_gestor(request: Request) -> GestorTrabajos:
    return request.app.state.jobs_manager


def _trabajo_propio(gestor: GestorTrabajos, job_id: str, workspace_id: str) -> dict:
    trabajo = gestor.estado(job_id)
    if (
        trabajo is None
        or trabajo["workspace_id"] != workspace_id
        or trabajo["generation_id"] is not None
        or trabajo["tipo"] not in TIPOS_COMUNES
    ):
        raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Trabajo no encontrado.")
    return trabajo


def _respuesta(trabajo: dict) -> TrabajoComunResponse:
    ruta = f"/api/jobs/{trabajo['job_id']}"
    error = None
    if trabajo["status"] not in ACTIVOS and trabajo["status"] != JobStatus.COMPLETED.value:
        error = {
            "code": trabajo["error_code"] or trabajo["status"].upper(),
            "message": trabajo["error_message"] or "El trabajo no se completó.",
        }
    return TrabajoComunResponse(
        job_id=trabajo["job_id"],
        status=trabajo["status"],
        status_url=ruta,
        events_url=f"{ruta}/events",
        cancel_url=f"{ruta}/cancel" if trabajo["status"] in ACTIVOS else None,
        posicion_cola=trabajo.get("posicion_cola"),
        result=(
            json.loads(trabajo["resultado"])
            if trabajo["status"] == JobStatus.COMPLETED.value and trabajo["resultado"] is not None
            else None
        ),
        error=error,
    )


@router.get("/{job_id}", response_model=TrabajoComunResponse, summary="Consultar trabajo")
def consultar(
    job_id: str,
    sesion: dict = Depends(get_sesion_actual),
    gestor: GestorTrabajos = Depends(get_gestor),
) -> TrabajoComunResponse:
    return _respuesta(_trabajo_propio(gestor, job_id, sesion["workspace_id"]))


@router.post("/{job_id}/cancel", response_model=TrabajoComunResponse, summary="Cancelar trabajo")
def cancelar(
    job_id: str,
    sesion: dict = Depends(get_sesion_actual),
    gestor: GestorTrabajos = Depends(get_gestor),
) -> TrabajoComunResponse:
    trabajo = _trabajo_propio(gestor, job_id, sesion["workspace_id"])
    if trabajo["status"] == JobStatus.CANCELLED.value:
        return _respuesta(trabajo)
    if not gestor.cancelar(job_id):
        raise ErrorAplicacion(ErrorCode.INVALID_STATE, "El trabajo ya terminó y no se puede cancelar.")
    # running continúa hasta que el ejecutor observe la cancelación cooperativa.
    return _respuesta(_trabajo_propio(gestor, job_id, sesion["workspace_id"]))


def _leer_stream(gestor: GestorTrabajos, manager: SessionManager, sesion: dict, job_id: str, ultimo_id: int):
    if manager.obtener_sesion(sesion["token"]) is None:
        raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión ya no está disponible.")
    trabajo = _trabajo_propio(gestor, job_id, sesion["workspace_id"])
    return trabajo, gestor.store.eventos_desde_job(job_id, ultimo_id)


async def _stream(
    request: Request, gestor: GestorTrabajos, manager: SessionManager, sesion: dict, job_id: str, ultimo_id: int
):
    heartbeat = time.monotonic()
    while not await request.is_disconnected():
        try:
            trabajo, eventos = await run_in_threadpool(_leer_stream, gestor, manager, sesion, job_id, ultimo_id)
        except ErrorAplicacion as error:
            # Headers ya enviados: informar el error y cerrar, sin revelar resultados.
            datos_error = {
                "error": error.error.model_dump(),
                "request_id": getattr(getattr(request, "state", None), "request_id", "sin-request"),
            }
            yield f"event: error\ndata: {json.dumps(datos_error)}\n\n"
            return
        for evento in eventos:
            datos = {clave: evento[clave] for clave in ("job_id", "step", "status", "iteration")}
            yield f"id: {evento['id']}\nevent: progress\ndata: {json.dumps(datos)}\n\n"
            ultimo_id = evento["id"]
        if trabajo["status"] not in ACTIVOS:
            return
        if time.monotonic() - heartbeat >= INTERVALO_HEARTBEAT:
            yield ": heartbeat\n\n"
            heartbeat = time.monotonic()
        await asyncio.sleep(INTERVALO_SONDEO)


@router.get(
    "/{job_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
    summary="Seguir progreso del trabajo",
)
def eventos(
    job_id: str,
    request: Request,
    ultimo_id: Annotated[int, Header(alias="Last-Event-ID", ge=0, le=2**63 - 1)] = 0,
    sesion: dict = Depends(get_sesion_actual),
    gestor: GestorTrabajos = Depends(get_gestor),
    manager: SessionManager = Depends(get_session_manager),
) -> StreamingResponse:
    _trabajo_propio(gestor, job_id, sesion["workspace_id"])
    return StreamingResponse(
        _stream(request, gestor, manager, sesion, job_id, ultimo_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
