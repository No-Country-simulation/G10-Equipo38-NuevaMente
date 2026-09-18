"""Esquema de eventos SSE de progreso (issue #03, referencia §3.3).

Qué es SSE (Server-Sent Events): un flujo HTTP de larga vida donde el servidor
va EMITIENDO eventos al cliente a medida que pasan (aquí: el avance de una
generación). GET /api/generations/{id}/events devuelve ``text/event-stream``
con eventos como este:

    id: 42
    event: progress
    data: {"generation_id": "gen_...", "step": "critic", "status": "running", "iteration": 2}

Este módulo define el modelo del ``data`` (el JSON de cada evento). El
encuadre SSE (las líneas ``id:``/``event:``/``data:`` y los heartbeats) lo
armará la capa de API con el issue #31.

Reglas de contrato que este modelo refleja (§3.3):

- ``id`` es MONOTÓNICO (creciente, sin repetir): permite a un cliente que se
  reconecta mandar ``Last-Event-ID`` y continuar donde estaba.
- ``iteration`` solo aparece cuando aplica (ciclos Writer-Critic, máximo 3
  por MAX_GENERATION_ATTEMPTS del Apéndice A).
- ``completed`` solo se emite DESPUÉS de confirmar la persistencia en OCI.
- Cerrar el stream NO cancela el trabajo: la cancelación es explícita.
- ``step`` aún no tiene enumeración cerrada en la documentación (los nodos
  del grafo son supervisor/researcher/writer/critic/finalizer, §5); se deja
  como string libre hasta que el issue #29 congele los nombres de pasos.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import JobStatus


class GenerationEvent(BaseModel):
    """Evento de progreso de una GENERACIÓN pedagógica (POST /api/generate).

    Discriminado del evento de trabajos comunes por su identificador:
    generaciones usan ``generation_id``; ingestión/chat/glosario usan
    ``job_id`` (ver JobEvent abajo y §Trabajos comunes de contratos-api.md).
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1, description="Identificador monotónico del evento dentro del stream.")
    generation_id: str = Field(description="Trabajo de generación al que pertenece el evento.")
    step: str = Field(description="Etapa del pipeline (supervisor/researcher/writer/critic/...).")
    status: JobStatus = Field(description="Estado del trabajo AL MOMENTO del evento.")
    iteration: int | None = Field(
        default=None,
        ge=1,
        description="Ciclo Writer-Critic (1..3) cuando aplica; None si el paso no itera.",
    )


class JobEvent(BaseModel):
    """Evento de progreso de un TRABAJO COMÚN (ingestión, chat, glosario).

    Misma forma que GenerationEvent pero con ``job_id``: el frontend distingue
    ambos streams por la clave del identificador (contratos-api.md, sección
    Trabajos comunes del issue #20).
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1, description="Identificador monotónico del evento dentro del stream.")
    job_id: str = Field(description="Trabajo común al que pertenece el evento.")
    step: str = Field(description="Etapa del trabajo (parse, chunking, embeddings, ...).")
    status: JobStatus = Field(description="Estado del trabajo AL MOMENTO del evento.")
    iteration: int | None = Field(
        default=None,
        ge=1,
        description="Reservado para pasos iterativos; None por defecto.",
    )
