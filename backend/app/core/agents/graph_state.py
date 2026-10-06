"""Contrato interno v1 del grafo; sus dependencias de ejecución no son estado."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.core.faithfulness.faithfulness import Juicio, ResultadoFidelidad
from app.jobs.manager import ContextoEjecucion
from app.schemas.enums import DocumentStatus, JobStatus, OutputLanguage, PedagogicalFormat
from app.schemas.internal import EvidenciaRecuperada, PresupuestoLlamadas, VerificacionVisual
from app.schemas.pedagogical import ContenidoAdaptado, ModeloContenido, Referencia
from app.schemas.requests import GenerateRequest
from app.schemas.responses import (
    AlmacenamientoOCI,
    DocumentoFuente,
    EvaluacionCalidad,
    IdiomaOrigen,
    Metadatos,
    PersistenciaInfo,
    Trazabilidad,
)


class ContratoGrafo(ModeloContenido):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DocumentoGeneracion(ContratoGrafo):
    """Metadata del registro autorizado, nunca aportada por el documento o LLM."""

    workspace_id: str = Field(min_length=1)
    fuente: DocumentoFuente
    estado: DocumentStatus
    idioma_origen: str
    secciones: tuple[str, ...] = ()
    vision_pendiente: bool = False
    contiene_material_visual: bool = False


@dataclass(frozen=True)
class DependenciasGrafo:
    """Pasar como context_schema/runtime.context al integrar LangGraph en #29."""

    ejecucion: ContextoEjecucion
    obtener_documento: Callable[[str, str], DocumentoGeneracion | None]


class RestriccionesGeneracion(ContratoGrafo):
    formato: PedagogicalFormat
    idioma_salida: OutputLanguage
    orientacion_perfil: str
    requisitos_formato: tuple[str, ...]
    max_redacciones: Literal[3] = 3
    max_llamadas: int = Field(default=20, gt=0, le=20)
    evidencia_exclusiva: Literal[True] = True
    ejemplos_etiquetados: Literal[True] = True
    citas_en_idioma_original: Literal[True] = True
    preservar_identificadores_tecnicos: Literal[True] = True


class RubricaSalida(ContratoGrafo):
    score_minimo: Literal[0.85] = 0.85
    dimensiones: tuple[str, ...] = (
        "claridad_pedagogica",
        "adecuacion_perfil",
        "cobertura_objetivos",
        "coherencia_didactica",
    )
    bloqueos: tuple[str, ...] = (
        "afirmacion_sin_respaldo",
        "contradiccion",
        "cita_invalida",
        "cobertura_incompleta",
        "evaluacion_incompleta",
        "verificacion_visual_insuficiente",
    )
    requiere_revision_visual: bool = False


class BorradorPedagogico(ContratoGrafo):
    contenido_adaptado: ContenidoAdaptado
    metadatos: Metadatos

    @model_validator(mode="after")
    def formato_coherente(self):
        if self.contenido_adaptado.tipo != self.metadatos.formato_generado.value:
            raise ValueError("El formato del borrador debe coincidir con sus metadatos")
        return self


class DiagnosticoGrafo(ContratoGrafo):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class EstadoGrafo(ContratoGrafo):
    """Estado Pydantic para StateGraph; updates reemplazan campos, sin append implícito.

    presupuesto.usadas refleja solicitudes reales (incluidos retries); las cuotas
    y el presupuesto se aplican en runtime, no al deserializar este contador.
    deadline usa el reloj monotónico del worker y no se reutiliza tras un reinicio.
    """

    graph_state_version: Literal["1.0"] = "1.0"
    schema_version: Literal["1.0"] = "1.0"
    workspace_id: str = Field(min_length=1)
    generation_id: str = Field(min_length=1)
    document_id: str | None = None
    parametros: GenerateRequest | None = None
    documento_fuente: DocumentoFuente | None = None
    source_hash: str | None = None
    idioma_origen: IdiomaOrigen | None = None
    secciones_disponibles: tuple[str, ...] = ()
    secciones_cubiertas: list[str] = Field(default_factory=list)
    evidencia: list[EvidenciaRecuperada] = Field(default_factory=list)
    referencias: list[Referencia] = Field(default_factory=list)
    restricciones: RestriccionesGeneracion | None = None
    rubrica: RubricaSalida | None = None
    borrador: BorradorPedagogico | None = None
    evaluacion_factual: ResultadoFidelidad | None = None
    evaluacion_visual: list[VerificacionVisual] = Field(default_factory=list)
    evaluacion_pedagogica: EvaluacionCalidad | None = None
    afirmaciones_fallidas: list[Juicio] = Field(default_factory=list)
    feedback: list[str] = Field(default_factory=list)
    intento: int = Field(default=0, ge=0, le=3)
    presupuesto: PresupuestoLlamadas = Field(default_factory=PresupuestoLlamadas)
    deadline: float = Field(gt=0)
    status: JobStatus = JobStatus.RUNNING
    error: DiagnosticoGrafo | None = None
    persistencia: PersistenciaInfo | None = None
    almacenamiento: AlmacenamientoOCI | None = None
    trazabilidad: Trazabilidad | None = None

    @model_validator(mode="after")
    def terminal_sin_borrador(self):
        if self.status in (JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.REJECTED_QUALITY) and self.borrador:
            raise ValueError("Un estado terminal no aprobado no conserva un borrador educativo")
        if self.status == JobStatus.COMPLETED and (
            self.persistencia is None or self.persistencia.status_upload != "completado" or self.almacenamiento is None
        ):
            raise ValueError("completed exige referencias de persistencia confirmada")
        return self
