"""Supervisor determinista: valida acceso y alcance y entrega políticas al grafo."""

from pydantic import ValidationError

from app.core.agents.graph_state import (
    DependenciasGrafo,
    DiagnosticoGrafo,
    EstadoGrafo,
    RestriccionesGeneracion,
    RubricaSalida,
)
from app.jobs.manager import DeadlineExcedidoError, TrabajoCanceladoError
from app.schemas.enums import DocumentStatus, JobStatus, PedagogicalFormat, RecipientProfile
from app.schemas.requests import GenerateRequest
from app.schemas.responses import IdiomaOrigen

_PERFILES = {
    RecipientProfile.PRINCIPIANTE: "Explicar fundamentos sin jerga sin definir; pasos breves y analogías etiquetadas.",
    RecipientProfile.JUNIOR_SSR: "Enfoque práctico, código sustentado por la fuente y errores frecuentes documentados.",
    RecipientProfile.LIDER_TECNICO: "Analizar arquitectura, decisiones, trade-offs y límites respaldados por la fuente.",
    RecipientProfile.EJECUTIVO: "Lenguaje accesible, impacto cualitativo y riesgos; no inventar ROI ni métricas.",
}
_FORMATOS = {
    PedagogicalFormat.TUTORIAL: ("pasos verificables", "resultado esperado", "prerrequisitos", "referencias por paso"),
    PedagogicalFormat.FLASHCARDS: (
        "un concepto por tarjeta",
        "frente y dorso",
        "IDs únicos",
        "referencias por tarjeta",
    ),
    PedagogicalFormat.QUIZ: (
        "cuatro opciones con IDs distintos",
        "una única respuesta defendible",
        "distractores deliberadamente falsos revisados aparte",
        "justificación y referencias por pregunta",
    ),
    PedagogicalFormat.RESUMEN_EJECUTIVO: (
        "puntos clave",
        "impacto cualitativo sustentado",
        "acciones sustentadas",
        "referencias",
    ),
    PedagogicalFormat.GUION_CLASE: ("objetivos observables", "escenas con duración positiva", "referencias por escena"),
}


def normalizar_idioma_origen(idioma: str) -> IdiomaOrigen:
    normalizado = idioma.strip().lower().replace("_", "-")
    if normalizado == "mixto":
        return "mixto"
    base = normalizado.split("-", 1)[0]
    if base not in ("es", "en", "pt"):
        raise ValueError("Idioma de origen no soportado; se admiten es, en, pt y mixto")
    return base


def _fallo(code: str, message: str, *, status: JobStatus = JobStatus.FAILED) -> dict:
    return {
        "status": status,
        "error": DiagnosticoGrafo(code=code, message=message),
        "borrador": None,
        "restricciones": None,
        "rubrica": None,
    }


def supervisor(estado: EstadoGrafo, dependencias: DependenciasGrafo) -> dict:
    """Devuelve un update de EstadoGrafo. El grafo #29 debe terminar ante failed/cancelled.

    obtener_documento recibe siempre el espacio del contexto autorizado. No se
    devuelve texto ni se llama a Gemini, storage, un reloj nuevo o un generador de IDs.
    """
    if estado.status != JobStatus.RUNNING:
        return {}
    ctx = dependencias.ejecucion
    try:
        ctx.chequear()
        if estado.workspace_id != ctx.workspace_id:
            return _fallo("NOT_FOUND", "Documento no disponible en este espacio.")
        solicitud = estado.parametros
        if solicitud is None or estado.document_id != solicitud.document_id:
            return _fallo("VALIDATION_ERROR", "Parámetros de generación inválidos.")
        documento = dependencias.obtener_documento(ctx.workspace_id, solicitud.document_id)
        ctx.chequear()
        if (
            documento is None
            or documento.workspace_id != ctx.workspace_id
            or documento.fuente.document_id != solicitud.document_id
        ):
            return _fallo("NOT_FOUND", "Documento no disponible en este espacio.")
        if documento.estado != DocumentStatus.READY or documento.vision_pendiente:
            return _fallo("INVALID_STATE", "El documento debe estar ready y sin interpretación visual pendiente.")
        if solicitud.alcance.tipo == "seccion" and solicitud.alcance.seccion_id not in documento.secciones:
            return _fallo("VALIDATION_ERROR", "La sección solicitada no pertenece al documento.")
        try:
            idioma_origen = normalizar_idioma_origen(documento.idioma_origen)
        except ValueError:
            return _fallo("VALIDATION_ERROR", "El idioma de origen no está soportado.")
        return {
            "documento_fuente": documento.fuente,
            "source_hash": documento.fuente.hash,
            "idioma_origen": idioma_origen,
            "secciones_disponibles": documento.secciones,
            "deadline": ctx.deadline,
            "restricciones": RestriccionesGeneracion(
                formato=solicitud.formato_salida,
                idioma_salida=solicitud.idioma_salida,
                orientacion_perfil=_PERFILES[solicitud.perfil_destinatario],
                requisitos_formato=_FORMATOS[solicitud.formato_salida],
                max_llamadas=min(20, estado.presupuesto.limite),
            ),
            "rubrica": RubricaSalida(requiere_revision_visual=documento.contiene_material_visual),
        }
    except TrabajoCanceladoError:
        return _fallo("CANCELLED", "El trabajo fue cancelado o el espacio fue retirado.", status=JobStatus.CANCELLED)
    except DeadlineExcedidoError:
        return _fallo("DEADLINE", "El trabajo excedió su deadline de ejecución.")


def crear_estado_inicial(
    solicitud: GenerateRequest | dict, *, generation_id: str, dependencias: DependenciasGrafo
) -> EstadoGrafo:
    """Frontera de entrada; fallos de parámetros son diagnósticos, nunca aprobación."""
    estado = EstadoGrafo(
        workspace_id=dependencias.ejecucion.workspace_id,
        generation_id=generation_id,
        deadline=dependencias.ejecucion.deadline,
    )
    try:
        parametros = GenerateRequest.model_validate(solicitud)
    except ValidationError:
        return EstadoGrafo.model_validate(
            {**estado.model_dump(), **_fallo("VALIDATION_ERROR", "Parámetros de generación inválidos.")}
        )
    estado.parametros = parametros
    estado.document_id = parametros.document_id
    return EstadoGrafo.model_validate({**estado.model_dump(), **supervisor(estado, dependencias)})
