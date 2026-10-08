"""Critic síncrono: juicios separados, política determinista y sin publicar borradores."""

import hashlib
import inspect
import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.config import Configuracion
from app.core.agents.critic_contracts import (
    Descomposicion,
    ImagenOriginal,
    JuiciosFactuales,
    ObtenerImagen,
    RevisionQuiz,
    RevisionVisual,
    RubricaPedagogica,
    VerificadorSync,
)
from app.core.agents.draft_validation import BorradorInvalidoError, parsear_json_estricto, validar_borrador
from app.core.agents.gemini_generation import GeneracionGeminiError
from app.core.agents.graph_state import DependenciasGrafo, DiagnosticoGrafo, EstadoGrafo
from app.core.agents.prompts import PROMPT_VERSION, preparar_prompts
from app.core.agents.supervisor import supervisor
from app.core.faithfulness.faithfulness import Afirmacion, EstadoEvaluacion, Juicio, VerificadorFidelidad
from app.core.rag.tokenizer import TokenizadorBPE
from app.jobs.manager import (
    CuotaAgotadaError,
    DeadlineExcedidoError,
    PresupuestoAgotadoError,
    ReintentableError,
    TrabajoCanceladoError,
)
from app.schemas.enums import JobStatus
from app.schemas.internal import VerificacionVisual
from app.schemas.responses import EvaluacionCalidad


@dataclass(frozen=True)
class DependenciasCritic:
    grafo: DependenciasGrafo = field(repr=False)
    proveedor: VerificadorSync = field(repr=False)
    configuracion: Configuracion = field(repr=False)
    obtener_imagen: ObtenerImagen | None = field(default=None, repr=False)

    def __post_init__(self):
        self.configuracion.validar_critico()
        if self.configuracion.mock_gemini != self.proveedor.es_mock:
            raise ValueError("MOCK_GEMINI debe coincidir con el proveedor explícito de Critic")
        for nombre in ("verificar_sync", "verificar_visual_sync"):
            if not callable(getattr(self.proveedor, nombre, None)) or inspect.iscoroutinefunction(
                getattr(self.proveedor, nombre)
            ):
                raise TypeError("Critic requiere un proveedor síncrono textual y visual")
        if inspect.iscoroutinefunction(self.obtener_imagen):
            raise TypeError("El loader visual debe ser síncrono")


class EvaluadorInvalidoError(ValueError):
    """Respuesta incompleta/incoherente del juez: fallo técnico, sin score."""


class EstadoCriticError(ValueError):
    def __init__(self, code, message, status=JobStatus.FAILED):
        self.code, self.message, self.status = code, message, status


def _revalidar(estado, dependencias):
    actualizado = supervisor(estado, dependencias.grafo)
    if "error" in actualizado:
        raise EstadoCriticError(actualizado["error"].code, actualizado["error"].message, actualizado["status"])
    if any(
        actualizado[campo] != getattr(estado, campo)
        for campo in (
            "source_hash",
            "documento_fuente",
            "idioma_origen",
            "secciones_disponibles",
            "rubrica",
        )
    ):
        raise EstadoCriticError("INVALID_STATE", "La fuente o su rúbrica cambiaron durante la revisión.")


def _segmentos(borrador, referencias):
    """Texto factual con ubicación y citas heredadas; distractores quedan fuera."""
    segmentos = {}
    omitidos = {
        "tipo",
        "id",
        "option_id",
        "correct_option_id",
        "perfil_aplicado",
        "formato_generado",
        "nicho_sector",
        "nivel_detalle",
        "idioma_origen",
        "idioma_salida",
        "seccion_id",
        "secciones_cubiertas",
    }

    def recorrer(valor, ruta, citas):
        if isinstance(valor, dict):
            citas = [r["chunk_id"] for r in valor.get("referencias", [])] or citas
            for clave, item in valor.items():
                if clave in omitidos or clave == "referencias":
                    continue
                if clave == "opciones" and "correct_option_id" in valor:
                    for indice, opcion in enumerate(item):
                        if opcion["option_id"] == valor["correct_option_id"]:
                            recorrer(opcion, f"{ruta}/opciones/{indice}", citas)
                else:
                    recorrer(item, ruta + "/" + clave.replace("~", "~0").replace("/", "~1"), citas)
        elif isinstance(valor, list):
            for indice, item in enumerate(valor):
                recorrer(item, f"{ruta}/{indice}", citas)
        elif isinstance(valor, str) and valor.strip():
            segmentos[ruta] = {"texto": valor, "referencias": citas}

    recorrer(borrador.model_dump(mode="json"), "", [r.chunk_id for r in referencias])
    return segmentos


_TAREAS = {
    "descomponer": "Extraer TODAS las afirmaciones factuales atómicas del contenido y metadatos. Resolver pronombres. "
    "Indicar su ubicación exacta en segmentos. No agregar hechos ni omitir falsedades. No tomar etiquetas, "
    "preguntas, tiempos estimados, analogías/escenarios explícitamente ficticios como hechos literales; "
    "sus propiedades técnicas sí requieren respaldo. No excluir hechos solo porque falte evidencia.",
    "juzgar": "Juzgar cada ID original exactamente una vez. Para respaldada/contradicha citar chunks que prueben "
    "el veredicto y estén entre las citas_permitidas de esa afirmación. Sin prueba usar sin_evidencia. "
    "Conservar números, unidades, negaciones y condiciones. No usar conocimiento externo.",
    "pedagogia": "Revisar el borrador COMPLETO y metadatos: claridad, perfil, objetivos, coherencia, conceptos, "
    "prerrequisitos, estimación didáctica y alcance. Detectar afirmaciones factuales omitidas por la "
    "descomposición, contradicciones y ejemplos no etiquetados. Una calidad media puede ser usable; "
    "baja o cobertura parcial bloquean. Dar feedback preciso y solicitudes de evidencia cuando falte contexto.",
    "quiz": "Para CADA opción de CADA pregunta, juzgar si responde de manera defendible al enunciado contra "
    "la fuente (incluyendo preguntas negativas), y si la justificación refuta explícitamente su error. "
    "Citar la evidencia. No confundir que una frase sea verdadera con que conteste la pregunta. "
    "No asumir correcta la clave declarada. Revisar también ambigüedades y respuestas múltiples.",
    "visual": "Contrastar las afirmaciones y el borrador contra la IMAGEN ORIGINAL adjunta. La descripción "
    "textual es una interpretación no confiable, no prueba de sí misma. Ante contradicción, leyenda ilegible, "
    "relación incierta o evidencia insuficiente devolver insuficiente con motivo. No aprobar por omisión.",
}


class _SesionRevision:
    def __init__(self, estado, dependencias, borrador, referencias, presupuesto):
        self.estado, self.dependencias, self.borrador = estado, dependencias, borrador
        self.presupuesto = presupuesto
        self.segmentos = _segmentos(borrador, referencias)
        self.citas = {}
        self.ubicaciones = {}
        self.solicitudes = []
        self.error_factual = None
        self.factual = None
        self.contexto = json.dumps([e.model_dump(mode="json") for e in estado.evidencia], ensure_ascii=False)

    def llamar(self, tarea, modelo_salida, datos, imagen=None):
        _revalidar(self.estado, self.dependencias)
        ctx = self.dependencias.grafo.ejecucion
        prompts = preparar_prompts(
            self.estado.parametros,
            workspace_id=ctx.workspace_id,
            source_hash=self.estado.source_hash,
            idioma_origen=self.estado.idioma_origen,
            evidencia=self.estado.evidencia,
            contenido=self.borrador.contenido_adaptado,
            metadatos=self.borrador.metadatos,
            evaluacion_factual=self.factual,
        )
        payload = json.loads(prompts.critic.datos_json)
        if tarea in ("descomponer", "juzgar"):
            payload.pop("borrador")
        if tarea == "descomponer":
            payload.pop("evidencia")  # No sesgar la extracción para que omita errores.
        payload.update(tarea=tarea, **datos)
        prompt = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        sistema = prompts.critic.system_instruction + "\nTarea actual: " + _TAREAS[tarea]
        schema = modelo_salida.model_json_schema()
        cfg = self.dependencias.configuracion
        max_output = cfg.generation_max_output_tokens
        tokens = TokenizadorBPE().contar(sistema + prompt + json.dumps(schema)) + max_output
        kwargs = dict(
            modelo=cfg.gemini_verification_model,
            system_instruction=sistema,
            response_json_schema=schema,
            max_output_tokens=max_output,
        )
        funcion = self.dependencias.proveedor.verificar_sync
        if imagen is not None:
            tokens += imagen.tokens_estimados
            kwargs.update(imagen_original=imagen.contenido, mime_type=imagen.mime_type)
            funcion = self.dependencias.proveedor.verificar_visual_sync

        def solicitar(*, timeout):
            # También entre retries: no reenviar evidencia de un recurso retirado.
            _revalidar(self.estado, self.dependencias)
            return funcion(prompt, timeout=timeout, **kwargs)

        respuesta = ctx.llamar(
            solicitar,
            modelo=cfg.gemini_verification_model,
            tokens_estimados=tokens,
            presupuesto=self.presupuesto,
        )
        _revalidar(self.estado, self.dependencias)
        try:
            datos = parsear_json_estricto(respuesta)
            return modelo_salida.model_validate_json(
                json.dumps(datos, ensure_ascii=False, allow_nan=False), strict=True
            )
        except (ValueError, TypeError, RecursionError) as error:
            raise EvaluadorInvalidoError("El juez devolvió una respuesta inválida") from error

    def descomponer(self, texto):
        try:
            respuesta = self.llamar("descomponer", Descomposicion, {"segmentos": self.segmentos})
            afirmaciones = []
            duplicados = set()
            for indice, dato in enumerate(respuesta.afirmaciones):
                clave = (dato.ubicacion, dato.texto.strip())
                if clave in duplicados:
                    raise EvaluadorInvalidoError("El extractor duplicó una afirmación")
                duplicados.add(clave)
                if dato.ubicacion not in self.segmentos:
                    raise EvaluadorInvalidoError("El extractor inventó una ubicación del borrador")
                identidad = hashlib.sha256(f"{dato.ubicacion}\0{dato.texto}\0{indice}".encode()).hexdigest()[:24]
                identificador = "af_" + identidad
                self.citas[identificador] = self.segmentos[dato.ubicacion]["referencias"]
                self.ubicaciones[identificador] = dato.ubicacion
                afirmaciones.append(Afirmacion(identificador, dato.texto))
            return afirmaciones
        except Exception as error:
            self.error_factual = error
            raise

    def juzgar(self, afirmaciones, contexto):
        try:
            respuesta = self.llamar(
                "juzgar",
                JuiciosFactuales,
                {
                    "afirmaciones": [
                        {"id": a.id, "texto": a.texto, "citas_permitidas": self.citas[a.id]} for a in afirmaciones
                    ]
                },
            )
            ids = [j.id_afirmacion for j in respuesta.juicios]
            if len(ids) != len(afirmaciones) or set(ids) != {a.id for a in afirmaciones}:
                raise EvaluadorInvalidoError("El juez no cubrió exactamente todas las afirmaciones")
            juicios = []
            for juicio in respuesta.juicios:
                if set(juicio.referencias) - set(self.citas[juicio.id_afirmacion]) or (
                    juicio.estado != "sin_evidencia" and not juicio.referencias
                ):
                    raise EvaluadorInvalidoError("El juicio no incluye citas válidas de la afirmación")
                if juicio.estado == "sin_evidencia":
                    self.solicitudes.append(f"Ampliar evidencia para {juicio.id_afirmacion}: {juicio.motivo}")
                juicios.append(
                    Juicio(
                        juicio.id_afirmacion,
                        juicio.estado == "respaldada",
                        f"{juicio.estado}: {juicio.motivo}",
                        tuple(juicio.referencias),
                    )
                )
            return juicios
        except Exception as error:
            self.error_factual = error
            raise

    def revisar_quiz(self):
        if self.borrador.contenido_adaptado.tipo != "quiz":
            return []
        preguntas = self.borrador.contenido_adaptado.preguntas
        respuesta = self.llamar("quiz", RevisionQuiz, {"preguntas": [p.model_dump(mode="json") for p in preguntas]})
        esperadas = {(p.id, o.option_id): p for p in preguntas for o in p.opciones}
        recibidas = [(o.pregunta_id, o.option_id) for o in respuesta.opciones]
        if len(recibidas) != len(esperadas) or set(recibidas) != set(esperadas):
            raise EvaluadorInvalidoError("El juez no revisó todas y solo las opciones del quiz")
        fallos = []
        for opcion in respuesta.opciones:
            pregunta = esperadas[(opcion.pregunta_id, opcion.option_id)]
            if set(opcion.referencias) - {r.chunk_id for r in pregunta.referencias} or (
                (opcion.defendible or opcion.refutada_por_explicacion) and not opcion.referencias
            ):
                raise EvaluadorInvalidoError("El juez del quiz no devolvió referencias válidas")
            correcta = opcion.option_id == pregunta.correct_option_id
            if opcion.defendible != correcta or opcion.refutada_por_explicacion == correcta:
                fallos.append(f"Quiz {pregunta.id}, opción {opcion.option_id}: {opcion.motivo}")
        return fallos

    def revisar_visual(self):
        chunks = [e.chunk for e in self.estado.evidencia if e.chunk.es_diagrama]
        necesaria = self.estado.rubrica.requiere_revision_visual or bool(chunks)
        if not necesaria:
            return "no_aplica", []
        revisiones = []
        for chunk in {c.chunk_id: c for c in chunks}.values():
            _revalidar(self.estado, self.dependencias)
            imagen = (
                None
                if self.dependencias.obtener_imagen is None
                else self.dependencias.obtener_imagen(
                    self.estado.workspace_id,
                    self.estado.document_id,
                    self.estado.source_hash,
                    chunk.chunk_id,
                )
            )
            if inspect.isawaitable(imagen):
                if inspect.iscoroutine(imagen):
                    imagen.close()
                raise EstadoCriticError("INVALID_STATE", "El loader visual debe devolver un resultado síncrono.")
            if imagen is None:
                revisiones.append(
                    VerificacionVisual(
                        chunk_id=chunk.chunk_id,
                        estado="insuficiente",
                        descripcion="Falta la imagen original autorizada.",
                    )
                )
                continue
            if not isinstance(imagen, ImagenOriginal) or (
                imagen.workspace_id,
                imagen.document_id,
                imagen.source_hash,
                imagen.chunk_id,
            ) != (self.estado.workspace_id, self.estado.document_id, self.estado.source_hash, chunk.chunk_id):
                raise EstadoCriticError("NOT_FOUND", "Imagen no disponible para el documento y espacio autorizados.")
            imagen = ImagenOriginal.model_validate(imagen.model_dump(), strict=True)
            respuesta = self.llamar("visual", RevisionVisual, {"chunk_visual": chunk.chunk_id}, imagen)
            if respuesta.chunk_id != chunk.chunk_id:
                raise EvaluadorInvalidoError("El juez visual respondió sobre otra imagen")
            revisiones.append(VerificacionVisual(**respuesta.model_dump()))
        estado_visual = "aprobada" if revisiones and all(r.estado == "aprobada" for r in revisiones) else "insuficiente"
        return estado_visual, revisiones


def critic(estado: EstadoGrafo, dependencias: DependenciasCritic) -> dict:
    """Update consumible por #29; aprobación conduce a Finalizer, nunca a completed."""
    if estado.status != JobStatus.RUNNING:
        return {}
    presupuesto = estado.presupuesto.model_copy()
    presupuesto.limite = min(presupuesto.limite, 20)

    def update(**campos):
        return {
            "borrador": None,
            "referencias": [],
            "evaluacion_factual": None,
            "evaluacion_visual": [],
            "evaluacion_pedagogica": None,
            "afirmaciones_fallidas": [],
            "feedback": [],
            "solicitudes_evidencia": [],
            "destino_revision": None,
            "error": None,
            "presupuesto": presupuesto,
            **campos,
        }

    def fallo(code, message, status=JobStatus.FAILED):
        return update(status=status, error=DiagnosticoGrafo(code=code, message=message))

    def devolver_revision(motivos, **campos):
        if estado.intento >= min(3, dependencias.configuracion.max_generation_attempts):
            return fallo(
                "QUALITY_REJECTED",
                "Se agotaron las redacciones sin superar la revisión de calidad.",
                JobStatus.REJECTED_QUALITY,
            )
        return update(
            borrador=estado.borrador,
            referencias=estado.referencias,
            feedback=motivos,
            destino_revision="writer",
            **campos,
        )

    try:
        try:
            EstadoGrafo.model_validate_json(estado.model_dump_json())
        except (ValueError, TypeError) as error:
            raise EstadoCriticError("INVALID_STATE", "El estado no cumple el contrato del grafo.") from error
        _revalidar(estado, dependencias)
        if not all((estado.borrador, estado.restricciones, estado.trazabilidad)) or estado.intento < 1:
            raise EstadoCriticError("INVALID_STATE", "Critic requiere un borrador válido de Writer y su trazabilidad.")
        if (estado.restricciones.formato, estado.restricciones.idioma_salida) != (
            estado.parametros.formato_salida,
            estado.parametros.idioma_salida,
        ):
            raise EstadoCriticError("INVALID_STATE", "Las restricciones no coinciden con la solicitud.")
        presupuesto.limite = min(presupuesto.limite, estado.restricciones.max_llamadas)
        if set(estado.secciones_cubiertas) - set(estado.secciones_disponibles):
            raise EstadoCriticError("INVALID_STATE", "La cobertura contiene secciones ajenas al documento.")
        try:
            borrador, referencias = validar_borrador(estado.borrador.model_dump_json(), estado)
        except BorradorInvalidoError as error:
            return devolver_revision([str(error)])
        sesion = _SesionRevision(estado, dependencias, borrador, referencias, presupuesto)
        factual = VerificadorFidelidad(sesion.descomponer, sesion.juzgar).verificar(
            json.dumps(sesion.segmentos, ensure_ascii=False),
            sesion.contexto,
        )
        # #24 captura errores de callbacks; preservar aquí cuota/deadline/cancelación originales.
        if sesion.error_factual is not None:
            raise sesion.error_factual
        if factual.estado != EstadoEvaluacion.EVALUABLE:
            if factual.estado == EstadoEvaluacion.NO_EVALUABLE and not factual.afirmaciones and factual.total == 0:
                return devolver_revision([factual.diagnostico], evaluacion_factual=factual)
            raise EvaluadorInvalidoError("La revisión factual no pudo completar una evaluación válida")
        sesion.factual = factual
        rubrica = sesion.llamar("pedagogia", RubricaPedagogica, {})
        fallos_quiz = sesion.revisar_quiz()
        visual, revisiones = sesion.revisar_visual()
        fallidas = [j for j in factual.juicios if not j.respaldada]
        razones = []
        feedback = list(rubrica.feedback)
        textos_afirmaciones = {a.id: a.texto for a in factual.afirmaciones}
        for juicio in fallidas:
            razones.append("afirmacion_sin_respaldo")
            feedback.append(
                f"Corregir {juicio.id_afirmacion} ({textos_afirmaciones[juicio.id_afirmacion]}) "
                f"en {sesion.ubicaciones[juicio.id_afirmacion]}: {juicio.motivo}; citas {list(juicio.referencias)}"
            )
        if factual.score < 0.70:
            razones.append("fidelidad_baja")
            feedback.append("Rehacer el borrador usando solo hechos respaldados y restricciones solicitadas.")
        elif factual.score < 0.85:
            razones.append("fidelidad_requiere_revision")
        for dimension in ("claridad_pedagogica", "adecuacion_perfil", "coherencia_didactica"):
            if getattr(rubrica, dimension) == "baja":
                razones.append(dimension)
                feedback.append(f"Mejorar {dimension} para el perfil solicitado.")
        necesarias = (
            set(estado.secciones_disponibles)
            if estado.parametros.alcance.tipo == "documento_completo"
            else {estado.parametros.alcance.seccion_id}
        )
        faltantes = sorted(necesarias - set(estado.secciones_cubiertas))
        solicitudes = [
            *sesion.solicitudes,
            *rubrica.solicitudes_evidencia,
            *[f"Recuperar sección {s}" for s in faltantes],
        ]
        cobertura = "parcial" if faltantes else rubrica.cobertura_objetivos
        if cobertura == "parcial":
            razones.append("cobertura_incompleta")
            feedback.append("Cubrir el alcance y objetivos pendientes con evidencia del documento.")
        if solicitudes:
            razones.append("evidencia_insuficiente")
        if rubrica.contradicciones or rubrica.afirmaciones_omitidas:
            razones.append("contradiccion_o_evaluacion_incompleta")
            feedback.extend(rubrica.contradicciones + rubrica.afirmaciones_omitidas)
        if fallos_quiz:
            razones.append("quiz_invalido")
            feedback.extend(fallos_quiz)
        if visual == "insuficiente":
            razones.append("verificacion_visual_insuficiente")
            feedback.append("Aportar/verificar las imágenes originales necesarias; no usar la descripción como prueba.")
        razones = list(dict.fromkeys(razones))
        aprobada = not razones
        evaluacion = EvaluacionCalidad(
            anclaje_fuente_score=factual.score,
            cantidad_afirmaciones=factual.total,
            cantidad_respaldadas=factual.respaldadas,
            estado_evaluacion="aprobada" if aprobada else "requiere_revision",
            claridad_pedagogica=rubrica.claridad_pedagogica,
            adecuacion_perfil=rubrica.adecuacion_perfil,
            cobertura_objetivos=cobertura,
            coherencia_didactica=rubrica.coherencia_didactica,
            verificacion_visual=visual,
            observaciones=rubrica.observaciones,
            razones_bloqueo=razones,
        )
        _revalidar(estado, dependencias)
        if not aprobada and estado.intento >= min(3, dependencias.configuracion.max_generation_attempts):
            return devolver_revision(feedback or razones)
        return update(
            borrador=borrador,
            referencias=referencias,
            evaluacion_factual=factual,
            evaluacion_visual=revisiones,
            evaluacion_pedagogica=evaluacion,
            afirmaciones_fallidas=fallidas,
            feedback=[] if aprobada else list(dict.fromkeys(feedback or razones)),
            solicitudes_evidencia=list(dict.fromkeys(solicitudes)),
            destino_revision="finalizer" if aprobada else "researcher" if solicitudes else "writer",
            trazabilidad=estado.trazabilidad.model_copy(
                update={
                    "modelo_verificacion": dependencias.configuracion.gemini_verification_model,
                    "prompt_version": PROMPT_VERSION,
                }
            ),
        )
    except EstadoCriticError as error:
        return fallo(error.code, error.message, error.status)
    except TrabajoCanceladoError:
        return fallo("CANCELLED", "Revisión cancelada o espacio retirado.", JobStatus.CANCELLED)
    except DeadlineExcedidoError:
        return fallo("DEADLINE", "La revisión excedió el deadline de ejecución.")
    except PresupuestoAgotadoError:
        return fallo("GENERATION_BUDGET", "La revisión agotó el presupuesto de solicitudes.")
    except CuotaAgotadaError:
        return fallo("RATE_LIMITED", "La cuota del modelo de verificación está agotada.")
    except (ReintentableError, GeneracionGeminiError, TimeoutError):
        return fallo("PROVIDER_UNAVAILABLE", "El evaluador no pudo completar la revisión.")
    except (EvaluadorInvalidoError, ValidationError):
        return fallo("INVALID_EVALUATION", "El evaluador devolvió una respuesta incompleta o inválida.")
    except (ValueError, TypeError):
        return fallo("INVALID_STATE", "El estado o la evidencia no cumplen el contrato de Critic.")
    except Exception:
        return fallo("INTERNAL", "Un fallo técnico impidió completar la revisión.")
