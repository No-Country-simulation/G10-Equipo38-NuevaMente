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


# con esta funcion normalizo la cadena del idioma de origen a un codigo estandar de dos letras o la etiqueta especial mixto
def normalizar_idioma_origen(idioma: str) -> IdiomaOrigen:
    normalizado = idioma.strip().lower().replace("_", "-")
    if normalizado == "mixto":
        return "mixto"
    base = normalizado.split("-", 1)[0]
    if base not in ("es", "en", "pt"):
        raise ValueError("Idioma de origen no soportado; se admiten es, en, pt y mixto")
    return base


# aqui construyo la estructura de fallo con el codigo de diagnostico para detener la ejecucion del grafo si una validacion no se cumple
def _fallo(code: str, message: str, *, status: JobStatus = JobStatus.FAILED) -> dict:
    return {
        "status": status,
        "error": DiagnosticoGrafo(code=code, message=message),
        "borrador": None,
        "restricciones": None,
        "rubrica": None,
    }


# con esta funcion actuo como el nodo supervisor en langgraph verificando autorizacion de acceso y politicas de alcance segun el issue 14
def supervisor(estado: EstadoGrafo, dependencias: DependenciasGrafo) -> dict:
    """Devuelve un update de EstadoGrafo. El grafo #29 debe terminar ante failed/cancelled.

    obtener_documento recibe siempre el espacio del contexto autorizado. No se
    devuelve texto ni se llama a Gemini, storage, un reloj nuevo o un generador de IDs.
    """
    if estado.status != JobStatus.RUNNING:
        # preparo la configuracion de politicas restricciones y rubrica de salida que guiaran al resto de agentes en el grafo
        return {}
    ctx = dependencias.ejecucion
    try:
        ctx.chequear()
        if estado.workspace_id != ctx.workspace_id:
            return _fallo("NOT_FOUND", "Documento no disponible en este espacio.")
        solicitud = estado.parametros
        doc_ids = (
            solicitud.document_ids
            if solicitud and solicitud.document_ids
            else (
                [solicitud.document_id]
                if solicitud and solicitud.document_id
                else (
                    estado.document_ids if estado.document_ids else ([estado.document_id] if estado.document_id else [])
                )
            )
        )
        if (
            solicitud is None
            or not doc_ids
            or (solicitud.document_id and estado.document_id and estado.document_id != solicitud.document_id)
        ):
            return _fallo("VALIDATION_ERROR", "Parámetros de generación inválidos.")

        docs = {}
        idiomas_origen = {}
        for d_id in doc_ids:
            documento = dependencias.obtener_documento(ctx.workspace_id, d_id)
            ctx.chequear()
            if documento is None or documento.workspace_id != ctx.workspace_id or documento.fuente.document_id != d_id:
                return _fallo("NOT_FOUND", "Documento no disponible en este espacio.")
            if documento.estado != DocumentStatus.READY or documento.vision_pendiente:
                return _fallo("INVALID_STATE", "El documento debe estar ready y sin interpretación visual pendiente.")
            try:
                lang = normalizar_idioma_origen(documento.idioma_origen)
                idiomas_origen[d_id] = lang
            except ValueError:
                return _fallo("VALIDATION_ERROR", "El idioma de origen no está soportado.")
            docs[d_id] = documento

        doc_principal = docs[doc_ids[0]]
        if solicitud.alcance.tipo == "seccion" and solicitud.alcance.seccion_id not in doc_principal.secciones:
            return _fallo("VALIDATION_ERROR", "La sección solicitada no pertenece al documento.")

        # Resolución de idioma colectivo: único o "mixto"
        idiomas_unicos = set(idiomas_origen.values())
        idioma_origen = list(idiomas_unicos)[0] if len(idiomas_unicos) == 1 else "mixto"

        requiere_revision_visual = False
        if solicitud.alcance.tipo == "seccion":
            requiere_revision_visual = doc_principal.contiene_material_visual and (
                doc_principal.secciones_visuales is None
                or solicitud.alcance.seccion_id in doc_principal.secciones_visuales
            )
        else:
            requiere_revision_visual = any(d.contiene_material_visual for d in docs.values())

        return {
            "document_id": doc_ids[0],
            "document_ids": doc_ids,
            "documento_fuente": doc_principal.fuente,
            "documentos_fuente": [docs[d_id].fuente for d_id in doc_ids],
            "source_hash": doc_principal.fuente.hash,
            "source_hashes": {d_id: docs[d_id].fuente.hash for d_id in doc_ids},
            "idioma_origen": idioma_origen,
            "idiomas_origen": idiomas_origen,
            "secciones_disponibles": doc_principal.secciones,
            "secciones_por_documento": {d_id: docs[d_id].secciones for d_id in doc_ids},
            "deadline": ctx.deadline,
            "restricciones": RestriccionesGeneracion(
                formato=solicitud.formato_salida,
                idioma_salida=solicitud.idioma_salida,
                orientacion_perfil=_PERFILES[solicitud.perfil_destinatario],
                requisitos_formato=_FORMATOS[solicitud.formato_salida],
                max_llamadas=min(20, estado.presupuesto.limite),
            ),
            "rubrica": RubricaSalida(requiere_revision_visual=requiere_revision_visual),
        }
    except TrabajoCanceladoError:
        return _fallo("CANCELLED", "El trabajo fue cancelado o el espacio fue retirado.", status=JobStatus.CANCELLED)
    except DeadlineExcedidoError:
        return _fallo("DEADLINE", "El trabajo excedió su deadline de ejecución.")


# con esta funcion construyo el estado inicial del grafo validando los parametros recibidos mediante el esquema de pydantic
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
