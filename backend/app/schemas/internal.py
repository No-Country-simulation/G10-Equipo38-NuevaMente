"""Contratos INTERNOS del backend (issue #03, tarea de contratos internos).

Diferencia con el resto de schemas/: estos modelos NO viajan por la API
pública y por lo tanto NO están congelados como contrato v1. Son el punto
de encuentro de los MÓDULOS del backend (guía §2): RAG produce Chunks y
Evidencia; agentes consumen Evidencia con un presupuesto de llamadas; la
ingesta produce un DocumentoParseado. Si algo de esto necesita exponerse
algún día por HTTP, se diseña su modelo público y se congela aparte.

Están en schemas/ (y no dentro de core/rag) porque cruzan varios módulos:
es la definición de "contrato compartido de carril" de la guía de trabajo.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.pedagogical import Referencia


class DocumentoParseado(BaseModel):
    """Resultado de la ingesta de un documento (issues #11 y #19).

    Lo produce el parser y lo consume el gestor de trabajos para decidir si
    el documento pasa a ready o a failed (§3.2 pasos 4-6).
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str
    titulo: str
    hash: str = Field(description="Huella del original; base de la deduplicación y trazabilidad.")
    extension: str = Field(description="'pdf', 'md' o 'txt' (los formatos aceptados).")
    cantidad_paginas: int = Field(ge=1)
    cantidad_tokens: int = Field(ge=1, description="Presupuesto consumido del límite de 100k tokens.")
    estado: DocumentStatus
    detalle_error: str | None = Field(default=None, description="Explicación accionable cuando estado=failed (§11.3).")


class Chunk(BaseModel):
    """Fragmento trazable del documento, unidad del índice vectorial (issue #12).

    El contrato del carril RAG (guía §2): documento, chunk, evidencia y
    filtros de acceso. ``chunk_id`` es ESTABLE: se referencia desde las
    citas de los formatos pedagógicos (Referencia) y desde la vista «Ver la
    fuente», así que regenerar el índice no puede cambiarlo.
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    indice: int = Field(ge=0, description="Posición del chunk dentro del documento.")
    texto: str = Field(min_length=1)
    pagina: int | None = Field(default=None, ge=1)
    seccion: str | None = None
    cantidad_tokens: int = Field(gt=0)
    es_diagrama: bool = Field(
        default=False, description="True si representa una página/imagen visual (ingesta multimodal #30)."
    )


class EvidenciaRecuperada(BaseModel):
    """Un chunk devuelto por el retriever, con su score y la consulta que lo trajo.

    Es la entrada del Writer y del verificador de fidelidad: las citas del
    contenido aprobado (Referencia) deben apuntar a chunks que pasaron por
    aquí (§19: el anclaje se mide contra la evidencia recuperada).
    """

    model_config = ConfigDict(extra="forbid")

    chunk: Chunk
    score: float = Field(ge=0.0, le=1.0, description="Similitud/relevancia del retrieval.")
    consulta: str = Field(description="Consulta temática del Researcher que recuperó este chunk.")

    def como_referencia(self) -> Referencia:
        """Proyecta la evidencia al formato de cita pública (Referencia)."""
        return Referencia(chunk_id=self.chunk.chunk_id, pagina=self.chunk.pagina, seccion=self.chunk.seccion)


class VerificacionVisual(BaseModel):
    """Resultado de interpretar un diagrama del documento (issue #30).

    La ingesta multimodal extrae páginas visuales; este contrato registra
    qué se concluyó de ellas para que evaluacion_calidad.verificacion_visual
    tenga sustento (§16.4).
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(description="Chunk visual verificado.")
    estado: str = Field(description="'aprobada' | 'insuficiente'; se proyecta a evaluacion_calidad.")
    descripcion: str = Field(description="Lo que el evaluador concluyó del diagrama.")


class PresupuestoLlamadas(BaseModel):
    """Control del límite de llamadas LLM por generación (§7.5: hasta 20).

    "Llamadas de revisión/redacción: hasta 20 solicitudes LLM por
    generación, incluidos reintentos". El grafo (issue #29) consulta
    ``disponibles`` antes de iniciar otro ciclo Writer-Critic: agotado el
    presupuesto, no se intenta de nuevo.
    """

    model_config = ConfigDict(extra="forbid")

    limite: int = Field(default=20, gt=0)
    usadas: int = Field(default=0, ge=0)

    @property
    def disponibles(self) -> int:
        """Llamadas que aún puede hacer el trabajo; nunca negativo."""
        return max(self.limite - self.usadas, 0)


class TrabajoComunResponse(BaseModel):
    """Respuesta de los trabajos comunes GET /api/jobs/{id} (issue #20).

    Ingestión, chat y glosario son vistas del MISMO gestor de la cola, no
    otra cola (contratos-api.md). ``result`` es genérico a propósito: cada
    tipo de trabajo define su propio resultado documentado.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    status: JobStatus
    status_url: str
    events_url: str
    cancel_url: str | None = None
    posicion_cola: int | None = Field(default=None, ge=1)
    result: dict | None = Field(default=None, description="Solo cuando aprobado y disponible.")
    error: dict | None = Field(default=None, description="Diagnóstico terminal cuando aplica.")
