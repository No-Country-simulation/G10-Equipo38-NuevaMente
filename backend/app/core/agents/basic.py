"""Un generador con RAG, sin LangGraph ni juez independiente; controles comunes."""

from dataclasses import replace

from app.core.agents.draft_validation import validar_borrador
from app.core.agents.graph_state import EstadoGrafo
from app.core.agents.researcher import researcher
from app.core.agents.supervisor import supervisor
from app.core.agents.writer import writer
from app.jobs.manager import DeadlineExcedidoError, FalloTrabajoError, TrabajoCanceladoError
from app.schemas.enums import JobStatus
from app.schemas.responses import (
    AlmacenamientoOCI,
    BasicPedagogicalOutput,
    EvaluacionNoRealizada,
    PersistenciaInfo,
    TrazabilidadBasica,
)
from app.storage.object_keys import clave_output
from app.storage.provider import StorageUnavailable


def ejecutar_basico(estado, grafo, investigador, escritor, persistir, *, bucket, created_at):
    ctx = grafo.ejecucion
    try:
        # Configuración por trabajo: no modifica límites ni cuotas compartidas.
        escritor = replace(
            escritor, configuracion=escritor.configuracion.model_copy(update={"max_generation_attempts": 1})
        )
        for nombre, ejecutar in (
            ("supervisor", lambda s: supervisor(s, grafo)),
            ("researcher", lambda s: researcher(s, investigador)),
            ("writer", lambda s: writer(s, escritor)),
        ):
            ctx.chequear()
            if nombre != "writer":
                ctx.registrar_paso(nombre, 1)
            estado = EstadoGrafo.model_validate({**estado.model_dump(), **ejecutar(estado)})
            ctx.chequear()
            if estado.status != JobStatus.RUNNING:
                if estado.status == JobStatus.CANCELLED:
                    raise TrabajoCanceladoError()
                raise FalloTrabajoError(estado.error.code, estado.error.message)
        autorizacion = supervisor(estado, grafo)
        campos_reval = ["source_hash", "documento_fuente", "idioma_origen", "secciones_disponibles"]
        if len(getattr(estado, "document_ids", None) or []) > 1:
            campos_reval.extend(["source_hashes", "documentos_fuente"])
        if autorizacion.get("error") or any(autorizacion.get(k) != getattr(estado, k) for k in campos_reval):
            raise FalloTrabajoError("INVALID_STATE", "La fuente cambió durante la generación.")
        borrador, referencias = validar_borrador(estado.borrador.model_dump_json(), estado)
        trazabilidad = estado.trazabilidad.model_dump()
        trazabilidad["modelo_verificacion"] = None
        fuentes = (
            [f.model_copy(deep=True) for f in estado.documentos_fuente]
            if estado.documentos_fuente
            else [estado.documento_fuente.model_copy(deep=True)]
        )
        paquete = BasicPedagogicalOutput(
            generation_id=estado.generation_id,
            metadatos=borrador.metadatos,
            documento_fuente=estado.documento_fuente,
            documentos_fuente=fuentes,
            contenido_adaptado=borrador.contenido_adaptado,
            evaluacion_calidad=EvaluacionNoRealizada(),
            referencias=referencias,
            created_at=created_at,
            trazabilidad=TrazabilidadBasica.model_validate(trazabilidad),
            almacenamiento_oci=AlmacenamientoOCI(
                bucket=bucket, objeto_id=clave_output(ctx.workspace_id, estado.generation_id)
            ),
        )
        ctx.registrar_paso("persist", 1)
        confirmacion = persistir(paquete, ctx)
        if not isinstance(confirmacion, PersistenciaInfo) or confirmacion.status_upload != "completado":
            raise StorageUnavailable("Persistencia no confirmada")
        ctx.chequear()
        return paquete
    except (TrabajoCanceladoError, DeadlineExcedidoError, FalloTrabajoError):
        raise
    except StorageUnavailable:
        raise FalloTrabajoError("STORAGE_UNAVAILABLE", "No se pudo confirmar la persistencia del material.") from None
    except Exception:
        raise FalloTrabajoError(
            "INVALID_RESPONSE", "El material no cumple el contrato estructural o de citas."
        ) from None
