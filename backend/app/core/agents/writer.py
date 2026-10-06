"""Writer síncrono: borradores completos, citas verificables y límites del grafo."""

import inspect
import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.config import Configuracion
from app.core.agents.gemini_generation import GeneracionGeminiError, GeneradorSync
from app.core.agents.graph_state import BorradorPedagogico, DependenciasGrafo, DiagnosticoGrafo, EstadoGrafo
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
from app.schemas.pedagogical import Referencia


@dataclass(frozen=True)
class DependenciasWriter:
    grafo: DependenciasGrafo = field(repr=False)
    proveedor: GeneradorSync = field(repr=False)
    configuracion: Configuracion = field(repr=False)

    def __post_init__(self):
        self.configuracion.validar_critico()
        if self.configuracion.mock_gemini != self.proveedor.es_mock:
            raise ValueError("MOCK_GEMINI debe coincidir con el proveedor explícito de Writer")
        if inspect.iscoroutinefunction(self.proveedor.generar_sync):
            raise TypeError("Writer requiere un proveedor síncrono")


class BorradorInvalidoError(ValueError):
    """Corrección pedagógica de estructura/citas, distinta del retry técnico."""


def _sin_duplicados(pares):
    resultado = {}
    for clave, valor in pares:
        if clave in resultado:
            raise BorradorInvalidoError("El JSON contiene claves duplicadas; entregar un objeto sin duplicados.")
        resultado[clave] = valor
    return resultado


def _constante_invalida(valor):
    raise BorradorInvalidoError("El JSON no admite NaN ni valores infinitos.")


def _validar_borrador(texto: str, estado: EstadoGrafo) -> tuple[BorradorPedagogico, list[Referencia]]:
    try:
        datos = json.loads(texto, object_pairs_hook=_sin_duplicados, parse_constant=_constante_invalida)
        validado = modelo_borrador(estado.parametros.formato_salida).model_validate_json(
            json.dumps(datos, ensure_ascii=False, allow_nan=False), strict=True
        )
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise BorradorInvalidoError(
            "Entregar JSON estricto con contenido y metadatos completos según el schema."
        ) from None
    solicitud = estado.parametros
    metadatos = validado.metadatos
    esperados = {
        "perfil_aplicado": solicitud.perfil_destinatario,
        "formato_generado": solicitud.formato_salida,
        "nicho_sector": solicitud.nicho_sector,
        "nivel_detalle": solicitud.nivel_detalle,
        "idioma_salida": solicitud.idioma_salida,
        "idioma_origen": estado.idioma_origen,
    }
    if any(getattr(metadatos, campo) != valor for campo, valor in esperados.items()):
        raise BorradorInvalidoError(
            "Los metadatos deben respetar perfil, formato, nicho, detalle e idiomas solicitados."
        )
    if metadatos.alcance.model_dump(exclude={"secciones_cubiertas"}) != solicitud.alcance.model_dump():
        raise BorradorInvalidoError("Conservar el alcance solicitado sin sustituir el documento o la sección.")
    if set(metadatos.alcance.secciones_cubiertas or []) != set(estado.secciones_cubiertas):
        raise BorradorInvalidoError("Declarar únicamente la cobertura recuperada por Researcher.")

    permitidas = {item.chunk.chunk_id: item.como_referencia() for item in estado.evidencia}
    referencias = {}
    contenido = validado.contenido_adaptado.model_dump(mode="json")

    def recorrer(valor):
        if isinstance(valor, dict):
            for campo, item in valor.items():
                if campo == "referencias":
                    for indice, cita in enumerate(item):
                        referencia = Referencia.model_validate(cita)
                        original = permitidas.get(referencia.chunk_id)
                        if original is None:
                            raise BorradorInvalidoError("Usar solo chunk_id presentes en la evidencia autorizada.")
                        if (referencia.pagina is not None and referencia.pagina != original.pagina) or (
                            referencia.seccion is not None and referencia.seccion != original.seccion
                        ):
                            raise BorradorInvalidoError(
                                "Las ubicaciones de las citas deben coincidir con la evidencia."
                            )
                        item[indice] = original.model_dump(mode="json")
                        referencias[original.chunk_id] = original
                else:
                    recorrer(item)
        elif isinstance(valor, list):
            for item in valor:
                recorrer(item)

    recorrer(contenido)
    metadatos.alcance.secciones_cubiertas = list(estado.secciones_cubiertas)
    return BorradorPedagogico(contenido_adaptado=contenido, metadatos=metadatos), list(referencias.values())


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
            )
            datos = json.loads(prompts.writer.datos_json)
            datos["restricciones"] = estado.restricciones.model_dump(mode="json")
            datos["secciones_cubiertas"] = list(estado.secciones_cubiertas)
            prompt = json.dumps(datos, ensure_ascii=False, allow_nan=False)
            schema = modelo_borrador(estado.parametros.formato_salida).model_json_schema()
            max_output = dependencias.configuracion.generation_max_output_tokens
            estimados = (
                TokenizadorBPE().contar(prompts.writer.system_instruction + prompt + json.dumps(schema)) + max_output
            )
            iniciada = False

            def solicitar(*, timeout):
                nonlocal redacciones, iniciada
                if not iniciada:
                    redacciones += 1
                    iniciada = True
                return dependencias.proveedor.generar_sync(
                    prompt,
                    modelo=dependencias.configuracion.gemini_generation_model,
                    system_instruction=prompts.writer.system_instruction,
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
            try:
                borrador, referencias = _validar_borrador(texto, estado)
            except BorradorInvalidoError as error:
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
        return fallo("VALIDATION_ERROR", "Writer agotó las redacciones sin producir un borrador válido con citas.")
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
