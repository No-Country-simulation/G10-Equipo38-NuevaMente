"""Empaquetado determinista del borrador revisado, sin generación pedagógica."""

from datetime import datetime

from app.core.agents.draft_validation import validar_borrador
from app.core.agents.graph_state import EstadoGrafo
from app.schemas.enums import JobStatus
from app.schemas.responses import AlmacenamientoOCI, PedagogicalOutput, ReviewedPedagogicalOutputV11
from app.storage.object_keys import clave_output


# aqui implemento el nodo finalizador que empaqueta y sella el contenido educativo una vez que supero todas las revisiones segun el issue 20
def finalizer(estado: EstadoGrafo, *, bucket: str, created_at: datetime) -> PedagogicalOutput:
    factual, calidad = estado.evaluacion_factual, estado.evaluacion_pedagogica
    if (
        estado.status != JobStatus.RUNNING
        or estado.destino_revision != "finalizer"
        or estado.borrador is None
        or estado.rubrica is None
        or estado.error is not None
        or factual is None
        or factual.score is None
        or factual.score < 0.85
        or factual.total == 0
        or factual.total != factual.respaldadas
        or estado.afirmaciones_fallidas
        or estado.solicitudes_evidencia
        or calidad is None
        or calidad.estado_evaluacion != "aprobada"
        or calidad.razones_bloqueo
        or calidad.cobertura_objetivos != "completa"
        or calidad.verificacion_visual == "insuficiente"
        or (estado.rubrica.requiere_revision_visual and calidad.verificacion_visual != "aprobada")
        or any(
            getattr(calidad, dimension) == "baja"
            for dimension in ("claridad_pedagogica", "adecuacion_perfil", "coherencia_didactica")
        )
        or created_at.tzinfo is None
    ):
        raise ValueError("Finalizer exige revisión completa y aprobada")
    # revalido nuevamente el borrador y sus citas para garantizar que el contenido no sufrio alteraciones posteriores a la revision
    borrador, referencias = validar_borrador(estado.borrador.model_dump_json(), estado)
    if borrador != estado.borrador or referencias != estado.referencias:
        raise ValueError("El contenido o sus citas cambiaron después de la revisión")
    # construyo el paquete final aprobado con sus copias profundas (deep copy) y la clave de almacenamiento en oci object storage
    modelo = ReviewedPedagogicalOutputV11 if estado.schema_version == "1.1" else PedagogicalOutput
    fuentes = (
        [f.model_copy(deep=True) for f in estado.documentos_fuente]
        if estado.documentos_fuente
        else [estado.documento_fuente.model_copy(deep=True)]
    )
    return modelo(
        schema_version=estado.schema_version,
        generation_id=estado.generation_id,
        status="aprobado",
        metadatos=borrador.metadatos.model_copy(deep=True),
        documento_fuente=estado.documento_fuente.model_copy(deep=True),
        documentos_fuente=fuentes,
        contenido_adaptado=borrador.contenido_adaptado.model_copy(deep=True),
        evaluacion_calidad=calidad.model_copy(deep=True),
        referencias=referencias,
        created_at=created_at,
        trazabilidad=estado.trazabilidad.model_copy(deep=True),
        almacenamiento_oci=AlmacenamientoOCI(
            bucket=bucket, objeto_id=clave_output(estado.workspace_id, estado.generation_id)
        ),
    )
