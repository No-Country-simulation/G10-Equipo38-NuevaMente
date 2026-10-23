"""Writer síncrono: borradores completos, citas verificables y límites del grafo."""

import inspect
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import Configuracion
from app.core.agents.draft_validation import BorradorInvalidoError, validar_borrador
from app.core.agents.gemini_generation import GeneracionGeminiError, GeneradorSync
from app.core.agents.graph_state import DependenciasGrafo, DiagnosticoGrafo, EstadoGrafo
from app.core.agents.prompts import modelo_borrador, preparar_prompts
from app.core.agents.supervisor import supervisor
from app.core.rag.tokenizer import TokenizadorBPE
from app.jobs.manager import (
    CuotaAgotadaError,
    DeadlineExcedidoError,
    PresupuestoAgotadoError,
    ReintentableError,
    TrabajoCanceladoError,
)
from app.schemas.enums import JobStatus

logger = logging.getLogger(__name__)


# aqui defino las dependencias inmutables del redactor validando que el proveedor de lenguaje sincrono coincida con la configuracion de pruebas
@dataclass(frozen=True)
class DependenciasWriter:
    grafo: DependenciasGrafo = field(repr=False)
    proveedor: GeneradorSync = field(repr=False)
    configuracion: Configuracion = field(repr=False)
    transformar_entrada: Callable[[str, str, dict], tuple[str, str, dict]] | None = field(default=None, repr=False)

    def __post_init__(self):
        self.configuracion.validar_critico()
        if self.configuracion.mock_gemini != self.proveedor.es_mock:
            raise ValueError("MOCK_GEMINI debe coincidir con el proveedor explícito de Writer")
        if inspect.iscoroutinefunction(self.proveedor.generar_sync):
            raise TypeError("Writer requiere un proveedor síncrono")
        if self.transformar_entrada is not None and (
            not callable(self.transformar_entrada) or inspect.iscoroutinefunction(self.transformar_entrada)
        ):
            raise TypeError("La adaptación de entrada requiere una función síncrona")


# aqui implemento el nodo redactor (writer) que genera el borrador educativo con referencias verificables segun el issue 16
def writer(estado: EstadoGrafo, dependencias: DependenciasWriter) -> dict:
    """Update del nodo; entrega un borrador para Critic, nunca aprobación/completed.

    Las correcciones locales consumen el mismo contador de tres redacciones que
    las revisiones de Critic. ctx.llamar reserva cuota y presupuesto por intento
    técnico; no se repite el pipeline ni se guardan respuestas inválidas.
    """
    if estado.status != JobStatus.RUNNING:
        return {}
    ctx = dependencias.grafo.ejecucion
    presupuesto = estado.presupuesto.model_copy()
    presupuesto.limite = min(presupuesto.limite, 20)
    redacciones = estado.intento
    feedback = list(estado.feedback)

    def update(**campos):
        return {
            "borrador": None,
            "referencias": [],
            "evaluacion_factual": None,
            "evaluacion_visual": [],
            "evaluacion_pedagogica": None,
            "afirmaciones_fallidas": [],
            "feedback": [],
            "destino_revision": None,
            "solicitudes_evidencia": [],
            "error": None,
            "intento": redacciones,
            "presupuesto": presupuesto,
            **campos,
        }

    def fallo(code, message, status=JobStatus.FAILED):
        return update(status=status, error=DiagnosticoGrafo(code=code, message=message))

    def revalidar():
        restricciones = supervisor(estado, dependencias.grafo)
        if "error" in restricciones:
            return fallo(restricciones["error"].code, restricciones["error"].message, restricciones["status"])
        if (
            restricciones.get("source_hash") != estado.source_hash
            or restricciones.get("documento_fuente") != estado.documento_fuente
            or restricciones.get("idioma_origen") != estado.idioma_origen
            or restricciones.get("documentos_fuente") != estado.documentos_fuente
            or restricciones.get("source_hashes") != estado.source_hashes
        ):
            return fallo("INVALID_STATE", "La versión de la fuente cambió durante la generación.")
        if estado.rubrica is not None and (
            restricciones["rubrica"].requiere_revision_visual != estado.rubrica.requiere_revision_visual
        ):
            return fallo("INVALID_STATE", "La rúbrica visual no coincide con la fuente autorizada.")
        return None

    try:
        invalido = revalidar()
        if invalido:
            return invalido
        if (
            estado.parametros is None
            or estado.restricciones is None
            or estado.rubrica is None
            or estado.trazabilidad is None
            or estado.idioma_origen is None
        ):
            return fallo("INVALID_STATE", "Writer requiere estado preparado, evidencia y trazabilidad de recuperación.")
        if (
            estado.restricciones.formato != estado.parametros.formato_salida
            or estado.restricciones.idioma_salida != estado.parametros.idioma_salida
        ):
            return fallo("INVALID_STATE", "Las restricciones no coinciden con los parámetros de generación.")
        presupuesto.limite = min(presupuesto.limite, estado.restricciones.max_llamadas)
        max_redacciones = min(
            3, estado.restricciones.max_redacciones, dependencias.configuracion.max_generation_attempts
        )
        if redacciones >= max_redacciones:
            return fallo("VALIDATION_ERROR", "Se agotaron las redacciones disponibles.", JobStatus.REJECTED_QUALITY)

        # ejecuto el ciclo de generacion permitiendo hasta tres intentos para corregir errores de estructura o citas
        while redacciones < max_redacciones:
            invalido = revalidar()
            if invalido:
                return invalido
            prompts = preparar_prompts(
                estado.parametros,
                workspace_id=ctx.workspace_id,
                source_hash=estado.source_hash,
                idioma_origen=estado.idioma_origen,
                evidencia=estado.evidencia,
                feedback=feedback,
                source_hashes=estado.source_hashes,
            )
            datos = json.loads(prompts.writer.datos_json)
            datos["restricciones"] = estado.restricciones.model_dump(mode="json")
            datos["secciones_cubiertas"] = list(estado.secciones_cubiertas)
            prompt = json.dumps(datos, ensure_ascii=False, allow_nan=False)
            schema = modelo_borrador(estado.parametros.formato_salida).model_json_schema()
            sistema = prompts.writer.system_instruction
            if dependencias.transformar_entrada is not None:
                sistema, prompt, schema = dependencias.transformar_entrada(sistema, prompt, schema)
            # La ubicación la resuelve el backend desde el chunk autorizado; el LLM
            # sólo elige su ID. El validador sigue rechazando ubicaciones falsas.
            # restrinjo el esquema json para que el modelo solo elija identificadores de fragmentos reales recuperados por el investigador
            referencia = schema["$defs"]["Referencia"]
            referencia["properties"] = {
                "chunk_id": {"type": "string", "enum": [e.chunk.chunk_id for e in estado.evidencia]}
            }
            referencia["required"] = ["chunk_id"]
            sistema += "\nEn las referencias devolver sólo chunk_id; el backend completa pagina y seccion originales."
            max_output = dependencias.configuracion.generation_max_output_tokens
            estimados = TokenizadorBPE().contar(sistema + prompt + json.dumps(schema)) + max_output
            iniciada = False

            # con esta funcion envio la peticion estructurada a gemini reservando cuota y presupuesto en el contexto de ejecucion
            def solicitar(*, timeout):
                nonlocal redacciones, iniciada
                if not iniciada:
                    redacciones += 1
                    iniciada = True
                    ctx.registrar_paso("writer", redacciones)
                return dependencias.proveedor.generar_sync(
                    prompt,
                    modelo=dependencias.configuracion.gemini_generation_model,
                    system_instruction=sistema,
                    response_json_schema=schema,
                    timeout=timeout,
                    max_output_tokens=max_output,
                )

            texto = ctx.llamar(
                solicitar,
                modelo=dependencias.configuracion.gemini_generation_model,
                tokens_estimados=estimados,
                presupuesto=presupuesto,
            )
            # valido la estructura del borrador y compruebo que todas las citas apunten a fragmentos autorizados
            try:
                borrador, referencias = validar_borrador(texto, estado)
            except BorradorInvalidoError as error:
                # Diagnóstico fijo de validación, nunca texto del borrador ni respuesta SDK.
                logger.warning("Writer: redacción %s descartada: %s", redacciones, error)
                feedback.append(str(error))
                continue
            invalido = revalidar()
            if invalido:
                return invalido
            trazabilidad = prompts.registrar_trazabilidad(
                estado.trazabilidad.model_copy(
                    update={"modelo_generacion": dependencias.configuracion.gemini_generation_model}
                )
            )
            return update(borrador=borrador, referencias=referencias, trazabilidad=trazabilidad)
        return fallo(
            "VALIDATION_ERROR",
            "Writer agotó las redacciones sin producir un borrador válido con citas.",
            JobStatus.REJECTED_QUALITY,
        )
    except TrabajoCanceladoError:
        return fallo("CANCELLED", "Generación cancelada o espacio retirado.", JobStatus.CANCELLED)
    except DeadlineExcedidoError:
        return fallo("DEADLINE", "La generación excedió su deadline de ejecución.")
    except PresupuestoAgotadoError:
        return fallo("GENERATION_BUDGET", "La generación agotó su presupuesto de solicitudes.")
    except CuotaAgotadaError:
        return fallo("RATE_LIMITED", "La cuota del modelo está agotada; no se realizan nuevas llamadas.")
    except (ReintentableError, GeneracionGeminiError):
        return fallo("PROVIDER_UNAVAILABLE", "El proveedor no pudo completar la generación.")
    except (ValueError, TypeError):
        return fallo("VALIDATION_ERROR", "El estado o la evidencia no cumplen el contrato de Writer.")
    except Exception:
        # Conserva contadores y descarta el borrador sin exponer payloads del SDK.
        return fallo("INTERNAL", "Un fallo interno impidió completar el nodo Writer.")
