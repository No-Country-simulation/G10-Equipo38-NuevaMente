"""Respuestas del contrato v1 (issue #03, referencia §16.3).

Aquí vive ``PedagogicalOutput``: el paquete educativo canónico que el
backend produce, valida DOS veces (antes del Critic y antes de persistir,
§16.4) y guarda en OCI. Es la ÚNICA fuente de verdad del contenido: Markdown
y PDF son vistas derivadas de este JSON (§16.3), nunca un segundo contrato.

Dos modelos que es fácil confundir (§16.3 los separa a propósito):

- ``PedagogicalOutput.status`` = "aprobado": el estado DEL ARTEFACTO
  educativo canónico. Si está en este paquete, pasó el Critic.
- ``GenerationJobResponse.status`` = JobStatus (queued/running/...): el
  estado DEL TRABAJO HTTP que transporta el paquete. completed solo cuando
  OCI confirmó la escritura (persistencia.status_upload="completado");
  un error de subida deja el trabajo failed aunque el contenido estuviera
  aprobado (§3.2: "un resultado redactado, uno aprobado y uno guardado son
  hechos diferentes").

Las claves JSON van en español porque SON el contrato público (§16.3).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.enums import (
    DetailLevel,
    IndustryNiche,
    JobStatus,
    OutputLanguage,
    PedagogicalFormat,
    RecipientProfile,
)
from app.schemas.errors import ErrorBody
from app.schemas.pedagogical import Alcance, ContenidoAdaptado, Referencia

# Idioma de ORIGEN detectado del documento (§16.3): más ancho que
# OutputLanguage porque la detección puede concluir "mixto" (§17.1 admite
# documentos mixtos dentro de es/en/pt). Es metadata; NUNCA sobreescribe la
# elección explícita del usuario (§17.2).
IdiomaOrigen = Literal["es", "en", "pt", "mixto"]


class Metadatos(BaseModel):
    """Ficha pedagógica del paquete (§16.3, tabla de metadatos).

    La UI usa estos campos para las tarjetas de resultado y para el estudio:
    conceptos para el glosario, objetivos para el progreso, tiempo estimado
    para planificar. ``tiempo_estimado_estudio_minutos`` es una ESTIMACIÓN
    pedagógica, no un tiempo medido.
    """

    model_config = ConfigDict(extra="forbid")

    perfil_aplicado: RecipientProfile
    formato_generado: PedagogicalFormat
    nicho_sector: IndustryNiche
    nivel_detalle: DetailLevel
    idioma_origen: IdiomaOrigen
    idioma_salida: OutputLanguage
    conceptos_clave: list[str] = Field(min_length=1, description="Conceptos principales cubiertos.")
    prerrequisitos: list[str] = Field(default_factory=list, description="Lista vacía si no aplica.")
    objetivos_aprendizaje: list[str] = Field(min_length=1, description="Resultados observables de aprendizaje.")
    tiempo_estimado_estudio_minutos: int = Field(gt=0, description="Estimación positiva, no tiempo medido.")
    alcance: Alcance = Field(description="Conserva la entrada y agrega secciones cubiertas.")


class DocumentoFuente(BaseModel):
    """Identificación del documento del que nace el paquete.

    Permite verificar que dos generaciones vienen de la MISMA versión del
    documento: el hash cambia si el archivo cambia (§16.3: "ID, título,
    hash, versión y procedencia disponible").
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    titulo: str = Field(min_length=1, max_length=300)
    hash: str = Field(min_length=1, description="Huella del archivo original, p. ej. 'sha256:...'.")
    version: str = Field(min_length=1, description="Versión del documento (p. ej. 'v1').")
    procedencia: str | None = Field(default=None, description="Procedencia declarada cuando está disponible.")


class EvaluacionCalidad(BaseModel):
    """Evaluación factual, pedagógica y visual del contenido (§16.4).

    El score anti-alucinación vive UNICAMENTE aquí
    (``anclaje_fuente_score``): §16.3 prohíbe repetirlo en el nivel raíz
    del paquete. Los campos de rúbrica usan escalas cerradas (no números
    sueltos) porque el Critic los produce cualitativamente y la UI los
    muestra como etiquetas.
    """

    model_config = ConfigDict(extra="forbid")

    anclaje_fuente_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Proporción de afirmaciones respaldadas por la fuente (§19). Umbral de aprobación: >= 0.85.",
    )
    cantidad_afirmaciones: int = Field(ge=1, description="Afirmaciones atómicas extraídas del contenido.")
    cantidad_respaldadas: int = Field(ge=0, description="Afirmaciones verificadas contra la evidencia.")
    estado_evaluacion: Literal["aprobada", "requiere_revision", "no_evaluable"]
    claridad_pedagogica: Literal["alta", "media", "baja"]
    adecuacion_perfil: Literal["alta", "media", "baja"]
    cobertura_objetivos: Literal["completa", "parcial"]
    coherencia_didactica: Literal["alta", "media", "baja"]
    verificacion_visual: Literal["no_aplica", "aprobada", "insuficiente"] = Field(
        description="Diagrams del documento (ingesta multimodal, issue #30)."
    )
    observaciones: str | None = Field(default=None, description="Notas breves del evaluador.")
    razones_bloqueo: list[str] | None = Field(default=None, description="Motivos cuando la evaluación no aprueba.")

    @model_validator(mode="after")
    def _respaldadas_no_superan_afirmaciones(self) -> "EvaluacionCalidad":
        """No pueden respaldarse más afirmaciones de las que existen."""
        if self.cantidad_respaldadas > self.cantidad_afirmaciones:
            raise ValueError(
                f"cantidad_respaldadas ({self.cantidad_respaldadas}) > "
                f"cantidad_afirmaciones ({self.cantidad_afirmaciones})"
            )
        return self


class ConfiguracionRetrieval(BaseModel):
    """Parámetros de la búsqueda MMR usada al reunir evidencia (issue #18)."""

    model_config = ConfigDict(extra="forbid")

    k: int = Field(gt=0, description="Fragmentos finales devueltos.")
    fetch_k: int = Field(gt=0, description="Candidatos pre-recuperados antes de diversificar.")
    lambda_mult: float = Field(ge=0.0, le=1.0, description="0 = máxima diversidad, 1 = máxima relevancia.")


class Trazabilidad(BaseModel):
    """Qué modelos y configuración produjeron el paquete (§16.3).

    Regla de privacidad: registra VERSIONES y evidencia, sin prompts
    completos, secretos ni razonamiento privado del modelo.
    """

    model_config = ConfigDict(extra="forbid")

    modelo_generacion: str
    modelo_verificacion: str
    modelo_embeddings: str
    prompt_version: str
    parser_version: str
    retrieval: ConfiguracionRetrieval


class AlmacenamientoOCI(BaseModel):
    """Dónde vive el paquete canónico en Object Storage (§16.3).

    El canónico solo NOMINA bucket y objeto; no confirma su propia escritura
    — esa confirmación (persistencia.status_upload) existe únicamente en la
    respuesta del trabajo, tras verificar el objeto (contratos-api.md).
    """

    model_config = ConfigDict(extra="forbid")

    bucket: str = Field(min_length=1)
    objeto_id: str = Field(min_length=1, description="Ruta del objeto, p. ej. 'outputs/{ws}/gen_.../content.json'.")


class PedagogicalOutput(BaseModel):
    """El paquete educativo canónico completo (schema_version 1.0).

    Este es el contrato más importante del proyecto: Writer lo produce
    tipado, Critic lo evalúa, Finalizer lo ensambla, la API lo entrega y
    OCI lo conserva. Congelado como v1 por el issue #03.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = Field(
        description="Versión del contrato; el versionado de la API vive aquí, no en /v1 (§7.1)."
    )
    generation_id: str = Field(min_length=1, description="Identificador generado por el backend.")
    status: Literal["aprobado"] = Field(description="El artefacto canónico solo existe aprobado (§16.3).")
    metadatos: Metadatos
    documento_fuente: DocumentoFuente
    contenido_adaptado: ContenidoAdaptado = Field(
        description="Unión discriminada por 'tipo' de los 5 formatos pedagógicos."
    )
    evaluacion_calidad: EvaluacionCalidad
    referencias: list[Referencia] = Field(min_length=1, description="Chunks y ubicaciones verificadas usadas en total.")
    created_at: datetime = Field(description="Fecha UTC de creación.")
    trazabilidad: Trazabilidad
    almacenamiento_oci: AlmacenamientoOCI


class PersistenciaInfo(BaseModel):
    """Estado de la escritura en OCI, SOLO en la respuesta del trabajo.

    ``status_upload="completado"`` es lo que el backend confirma verificando
    el objeto; por eso el trabajo puede ser completed. En desarrollo con
    MOCK_OCI=1 indica provider=mock y NO afirma completado en OCI real
    (regla 2 de contratos-api.md).
    """

    model_config = ConfigDict(extra="forbid")

    status_upload: Literal["completado", "pendiente", "fallido"]
    provider: Literal["oci", "mock"] | None = Field(default=None, description="Mock explícito para desarrollo/CI (§8).")


class GenerationJobResponse(BaseModel):
    """Respuesta de GET /api/generations/{id}: el trabajo que transporta el paquete.

    ``contenido`` (el PedagogicalOutput aprobado) solo existe cuando el
    status es completed. Un rejected_quality se consulta con HTTP 200 y
    diagnóstico en ``error`` — el problema es del resultado, no de la
    consulta (§7.3).
    """

    model_config = ConfigDict(extra="forbid")

    generation_id: str = Field(min_length=1)
    status: JobStatus
    status_url: str = Field(description="URL de consulta del trabajo (§3.3).")
    events_url: str = Field(description="URL del stream SSE de progreso (§3.3).")
    cancel_url: str | None = Field(default=None)
    posicion_cola: int | None = Field(default=None, ge=1, description="Visible mientras está en cola.")
    contenido: PedagogicalOutput | None = Field(
        default=None, description="Paquete aprobado; solo presente cuando status=completed."
    )
    persistencia: PersistenciaInfo | None = Field(
        default=None, description="Agregado por el backend cuando status=completed (§16.3)."
    )
    error: ErrorBody | None = Field(default=None, description="Diagnóstico terminal cuando aplica.")
