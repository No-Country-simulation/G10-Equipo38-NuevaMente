"""Respuestas internas del juez; el modelo nunca decide score ni aprobación."""

from collections.abc import Callable
from typing import Annotated, Literal, Protocol

from pydantic import Field, StringConstraints

from app.core.agents.graph_state import ContratoGrafo

TextoRevision = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
NotaRevision = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Nivel = Literal["alta", "media", "baja"]


class AfirmacionExtraida(ContratoGrafo):
    texto: TextoRevision
    ubicacion: str = Field(min_length=1)


class Descomposicion(ContratoGrafo):
    afirmaciones: list[AfirmacionExtraida] = Field(max_length=256)


class VeredictoFactual(ContratoGrafo):
    id_afirmacion: str = Field(min_length=1)
    estado: Literal["respaldada", "contradicha", "sin_evidencia"]
    motivo: NotaRevision
    referencias: list[str]


class JuiciosFactuales(ContratoGrafo):
    juicios: list[VeredictoFactual]


class RubricaPedagogica(ContratoGrafo):
    claridad_pedagogica: Nivel
    adecuacion_perfil: Nivel
    cobertura_objetivos: Literal["completa", "parcial"]
    coherencia_didactica: Nivel
    contradicciones: list[NotaRevision] = Field(max_length=20)
    afirmaciones_omitidas: list[NotaRevision] = Field(max_length=20)
    solicitudes_evidencia: list[NotaRevision] = Field(max_length=20)
    feedback: list[NotaRevision] = Field(max_length=20)
    observaciones: NotaRevision | None


class OpcionRevisada(ContratoGrafo):
    pregunta_id: str = Field(min_length=1)
    option_id: str = Field(min_length=1)
    defendible: bool
    refutada_por_explicacion: bool
    motivo: NotaRevision
    referencias: list[str]


class RevisionQuiz(ContratoGrafo):
    opciones: list[OpcionRevisada]


class ImagenOriginal(ContratoGrafo):
    """Imagen obtenida por un loader autorizado (#30), nunca generada por el juez."""

    workspace_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    contenido: bytes = Field(min_length=1, max_length=20_000_000, repr=False)
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]
    tokens_estimados: int = Field(gt=0, description="Reserva visual estimada por el loader autorizado.")


class RevisionVisual(ContratoGrafo):
    chunk_id: str = Field(min_length=1)
    estado: Literal["aprobada", "insuficiente"]
    descripcion: NotaRevision


ObtenerImagen = Callable[[str, str, str, str], ImagenOriginal | None]


class VerificadorSync(Protocol):
    es_mock: bool

    def verificar_sync(
        self,
        prompt: str,
        *,
        modelo: str,
        system_instruction: str,
        response_json_schema: dict,
        timeout: float,
        max_output_tokens: int,
    ) -> str: ...

    def verificar_visual_sync(
        self,
        prompt: str,
        *,
        imagen_original: bytes,
        mime_type: str,
        modelo: str,
        system_instruction: str,
        response_json_schema: dict,
        timeout: float,
        max_output_tokens: int,
    ) -> str: ...
