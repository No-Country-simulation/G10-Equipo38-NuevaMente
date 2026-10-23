"""Researcher determinista (#27): cobertura explícita, evidencia como datos."""

import inspect
from dataclasses import dataclass, field

from app.core.agents.graph_state import DependenciasGrafo, DiagnosticoGrafo, EstadoGrafo
from app.core.agents.prompts import cargar_plantilla
from app.core.agents.supervisor import supervisor
from app.core.rag.retriever import RetrieverMMR, identidad_texto, tokens_evidencia
from app.core.rag.vectorstore import IndiceInconsistenteError, seccion_del_chunk
from app.jobs.manager import CuotaAgotadaError, DeadlineExcedidoError, ReintentableError, TrabajoCanceladoError
from app.schemas.enums import JobStatus
from app.schemas.responses import ConfiguracionRetrieval, Trazabilidad

_FORMATOS = {
    "tutorial": (
        "pasos configuración prerrequisitos verificación",
        "steps configuration prerequisites validation",
        "passos configuração pré-requisitos verificação",
    ),
    "flashcards": (
        "definiciones conceptos relaciones",
        "definitions concepts relationships",
        "definições conceitos relações",
    ),
    "quiz": (
        "reglas diferencias errores conceptos",
        "rules differences errors concepts",
        "regras diferenças erros conceitos",
    ),
    "resumen_ejecutivo": (
        "objetivos decisiones riesgos limitaciones",
        "objectives decisions risks limitations",
        "objetivos decisões riscos limitações",
    ),
    "guion_clase": ("conceptos secuencia ejemplos", "concepts sequence examples", "conceitos sequência exemplos"),
}
_PERFILES = {
    "principiante": ("fundamentos términos básicos", "foundations basic terms", "fundamentos termos básicos"),
    "junior_ssr": (
        "implementación configuración diagnóstico",
        "implementation configuration troubleshooting",
        "implementação configuração diagnóstico",
    ),
    "lider_tecnico": (
        "arquitectura restricciones alternativas",
        "architecture constraints alternatives",
        "arquitetura restrições alternativas",
    ),
    "ejecutivo": ("impacto riesgos recursos", "impact risks resources", "impacto riscos recursos"),
}


# aqui defino las dependencias inmutables del nodo investigador asegurando que los metodos de recuperacion sean sincronos
@dataclass(frozen=True)
class DependenciasResearcher:
    grafo: DependenciasGrafo = field(repr=False)
    retriever: RetrieverMMR = field(repr=False)
    parser_version: str
    pregunta: str | None = None

    def __post_init__(self):
        if not self.parser_version.strip():
            raise ValueError("Se requiere la versión de parser registrada por la ingestión")
        if self.pregunta is not None and not 1 <= len(self.pregunta.strip()) <= 2000:
            raise ValueError("La pregunta debe contener entre 1 y 2000 caracteres")
        for funcion in (self.retriever.recuperar, self.retriever.indice.listar_chunks):
            if not callable(funcion) or inspect.iscoroutinefunction(funcion):
                raise TypeError("Researcher requiere recuperación e inventario síncronos")


class _Bloqueo(Exception):
    def __init__(self, code, message, status=JobStatus.FAILED):
        self.code, self.message, self.status = code, message, status


# con esta funcion construyo una consulta de busqueda vectorial contextualizada segun el perfil y formato pedagogico sin generar instrucciones directas
def _consulta(estado: EstadoGrafo, seccion: str, *, ampliada: bool = False, titulo_seccion: str = "") -> str:
    idioma = {"es": 0, "en": 1, "pt": 2}.get(estado.idioma_origen, 0)
    solicitud = estado.parametros
    formato = _FORMATOS[solicitud.formato_salida.value][idioma]
    perfil = _PERFILES[solicitud.perfil_destinatario.value][idioma]
    titulo = estado.documento_fuente.titulo[:300]
    if ampliada:
        formato = (
            "definiciones relaciones ejemplos",
            "definitions relationships examples",
            "definições relações exemplos",
        )[idioma]
    # Son términos de una búsqueda vectorial; jamás se ejecutan como instrucciones.
    return f"{seccion} {titulo_seccion} | {titulo} | {formato} | {perfil}"[:2000]


# aqui implemento el nodo researcher del grafo que recupera evidencia relevante respetando las cuotas de tokens segun el issue 15
def researcher(estado: EstadoGrafo, dependencias: DependenciasResearcher) -> dict:
    """Update del estado, sin modificarlo ni redactar/aprobar material pedagógico.

    Prioriza un fragmento por sección; completa después con evidencia diversa.
    Puede ampliar una consulta vacía o una solicitud de Critic dentro del límite.
    El grafo #29 debe terminar ante failed/cancelled/rejected_quality.
    """
    if estado.status != JobStatus.RUNNING:
        return {}
    ctx, recuperador = dependencias.grafo.ejecucion, dependencias.retriever

    def update(**campos):
        return {
            "evidencia": [],
            "secciones_cubiertas": [],
            "borrador": None,
            "referencias": [],
            "evaluacion_factual": None,
            "evaluacion_visual": [],
            "evaluacion_pedagogica": None,
            "afirmaciones_fallidas": [],
            "destino_revision": None,
            "solicitudes_evidencia": [],
            "error": None,
            "trazabilidad": None,
            **campos,
        }

    # con esta funcion interna confirmo que el estado del documento y los permisos sigan vigentes antes de cada recuperacion
    def revalidar():
        actual = supervisor(estado, dependencias.grafo)
        if "error" in actual:
            raise _Bloqueo(actual["error"].code, actual["error"].message, actual["status"])
        campos = [
            "source_hash",
            "documento_fuente",
            "idioma_origen",
            "secciones_disponibles",
            "restricciones",
            "rubrica",
        ]
        if len(estado.document_ids) > 1:
            campos.extend(["source_hashes", "documentos_fuente"])
        if any(actual.get(c) != getattr(estado, c) for c in campos):
            raise _Bloqueo("INVALID_STATE", "La fuente o el alcance cambiaron durante la investigación.")

    try:
        revalidar()
        if estado.parametros is None or estado.source_hash is None or estado.documento_fuente is None:
            raise _Bloqueo("INVALID_STATE", "Researcher requiere el estado preparado por Supervisor.")
        if estado.intento >= 3:
            raise _Bloqueo("MAX_ATTEMPTS", "Se agotaron las tres redacciones disponibles.", JobStatus.REJECTED_QUALITY)

        doc_ids = estado.document_ids or ([estado.document_id] if estado.document_id else [])
        es_multi = len(doc_ids) > 1

        chunks = []
        if es_multi:
            for d_id in doc_ids:
                d_hash = estado.source_hashes.get(d_id, estado.source_hash)
                d_chunks = recuperador.indice.listar_chunks(
                    ctx.workspace_id,
                    d_id,
                    contexto=ctx,
                    source_hash=d_hash,
                )
                if not d_chunks:
                    raise _Bloqueo("INVALID_STATE", f"El documento {d_id} no tiene un índice disponible.")
                chunks.extend(d_chunks)
        else:
            chunks = recuperador.indice.listar_chunks(
                ctx.workspace_id,
                estado.document_id,
                contexto=ctx,
                source_hash=estado.source_hash,
            )
            if not chunks:
                raise _Bloqueo("INVALID_STATE", "El documento no tiene un índice disponible.")

        revalidar()
        originales = {c.chunk_id: c for c in chunks}
        evidencia, ids, textos, omitidos = [], set(), set(), set()
        max_tokens = recuperador.max_tokens

        def agregar(item):
            clave = (item.chunk.document_id, identidad_texto(item.chunk.texto))
            if item.chunk.chunk_id in ids or clave in textos:
                return False
            if tokens_evidencia([*evidencia, item], recuperador.tokenizador) > max_tokens:
                omitidos.add(item.chunk.chunk_id)
                return False
            evidencia.append(item)
            ids.add(item.chunk.chunk_id)
            textos.add(clave)
            return True

        if not es_multi:
            secciones = tuple(dict.fromkeys(seccion_del_chunk(c) for c in chunks))
            if set(secciones) != set(estado.secciones_disponibles):
                raise _Bloqueo(
                    "INVALID_STATE", "Las secciones del registro y del índice no coinciden; reconstruir el índice."
                )
            alcance = estado.parametros.alcance
            objetivo = [alcance.seccion_id] if alcance.tipo == "seccion" else list(secciones)
            pools = {s: [] for s in objetivo}
            titulos = {seccion_del_chunk(c): c.seccion or seccion_del_chunk(c) for c in chunks}

            def validar(item):
                original = originales.get(item.chunk.chunk_id)
                if original is None or original != item.chunk or seccion_del_chunk(item.chunk) not in pools:
                    raise IndiceInconsistenteError("Evidencia ajena al inventario o alcance autorizados")
                return item

            def consultar(consulta, seccion, presupuesto):
                revalidar()
                resultado = recuperador.recuperar(
                    ctx.workspace_id,
                    estado.document_id,
                    consulta,
                    contexto=ctx,
                    seccion=seccion,
                    presupuesto_tokens=presupuesto,
                    source_hash=estado.source_hash,
                )
                revalidar()
                omitidos.update(resultado.chunks_omitidos)
                return [validar(item) for item in resultado.evidencia]

            for item in estado.evidencia:
                item = validar(item)
                pools[seccion_del_chunk(item.chunk)].append(item)
            faltantes = []

            def consulta_seccion(seccion, ampliada=False):
                base = _consulta(estado, seccion, ampliada=ampliada, titulo_seccion=titulos[seccion])
                return ((dependencias.pregunta or "") + " | " + base)[:2000] if dependencias.pregunta else base

            for seccion in objetivo:
                if not pools[seccion]:
                    restante = max_tokens - tokens_evidencia(evidencia, recuperador.tokenizador)
                    if restante > 0:
                        pools[seccion] = consultar(consulta_seccion(seccion), seccion, restante)
                    else:
                        omitidos.update(c.chunk_id for c in chunks if seccion_del_chunk(c) == seccion)
                elegido = next((item for item in pools[seccion] if agregar(item)), None)
                if elegido is None:
                    restante = max_tokens - tokens_evidencia(evidencia, recuperador.tokenizador)
                    if restante > 0:
                        ampliacion = consultar(consulta_seccion(seccion, True), seccion, restante)
                        pools[seccion].extend(ampliacion)
                        elegido = next((item for item in ampliacion if agregar(item)), None)
                if elegido is None:
                    faltantes.append(seccion)
            if faltantes:
                code = "EVIDENCE_BUDGET" if omitidos else "COVERAGE_INCOMPLETE"
                raise _Bloqueo(
                    code,
                    "No se pudo reunir evidencia para estas secciones: "
                    + ", ".join(faltantes)
                    + ". Acotá el alcance o aumentá el presupuesto dentro del límite permitido.",
                    JobStatus.REJECTED_QUALITY,
                )

            for consulta in dict.fromkeys(estado.solicitudes_evidencia):
                restante = max_tokens - tokens_evidencia(evidencia, recuperador.tokenizador)
                if restante <= 0:
                    raise _Bloqueo(
                        "EVIDENCE_BUDGET",
                        "No queda presupuesto para ampliar la evidencia solicitada.",
                        JobStatus.REJECTED_QUALITY,
                    )
                nuevos = consultar(consulta, alcance.seccion_id if alcance.tipo == "seccion" else None, restante)
                if not nuevos:
                    raise _Bloqueo(
                        "COVERAGE_INCOMPLETE",
                        "No se encontró evidencia para una ampliación solicitada por Critic.",
                        JobStatus.REJECTED_QUALITY,
                    )
                for item in nuevos:
                    agregar(item)

            for ronda in range(max((len(pool) for pool in pools.values()), default=0)):
                for pool in pools.values():
                    ctx.chequear()
                    if ronda < len(pool):
                        agregar(pool[ronda])
        else:
            # Flujo multi-documental: investigación equitativa entre fuentes autorizadas
            pools = {d_id: [] for d_id in doc_ids}
            titulos = {
                d_id: next((f.titulo for f in estado.documentos_fuente if f.document_id == d_id), d_id)
                for d_id in doc_ids
            }

            def validar_multi(item):
                original = originales.get(item.chunk.chunk_id)
                if original is None or original != item.chunk or item.chunk.document_id not in pools:
                    raise IndiceInconsistenteError("Evidencia ajena al inventario o alcance autorizados")
                return item

            def consultar_multi(consulta, presupuesto):
                revalidar()
                resultado = recuperador.recuperar_multidocumental(
                    ctx.workspace_id,
                    doc_ids,
                    consulta,
                    contexto=ctx,
                    presupuesto_tokens=presupuesto,
                    source_hashes=estado.source_hashes,
                )
                revalidar()
                omitidos.update(resultado.chunks_omitidos)
                return [validar_multi(item) for item in resultado.evidencia]

            for item in estado.evidencia:
                item = validar_multi(item)
                pools[item.chunk.document_id].append(item)

            idioma_idx = {"es": 0, "en": 1, "pt": 2}.get(estado.idioma_origen, 0)
            solicitud = estado.parametros
            formato = _FORMATOS[solicitud.formato_salida.value][idioma_idx]
            perfil = _PERFILES[solicitud.perfil_destinatario.value][idioma_idx]

            faltantes = []
            for d_id in doc_ids:
                if not pools[d_id]:
                    restante = max_tokens - tokens_evidencia(evidencia, recuperador.tokenizador)
                    if restante > 0:
                        consulta_doc = f"{titulos[d_id][:300]} | {formato} | {perfil}"[:2000]
                        if dependencias.pregunta:
                            consulta_doc = f"{dependencias.pregunta} | {consulta_doc}"[:2000]
                        res = consultar_multi(consulta_doc, restante)
                        for r in res:
                            pools[r.chunk.document_id].append(r)
                elegido = next((item for item in pools[d_id] if agregar(item)), None)
                if elegido is None:
                    faltantes.append(d_id)

            if faltantes:
                code = "EVIDENCE_BUDGET" if omitidos else "COVERAGE_INCOMPLETE"
                raise _Bloqueo(
                    code,
                    "No se pudo reunir evidencia para todas las fuentes autorizadas: " + ", ".join(faltantes) + ".",
                    JobStatus.REJECTED_QUALITY,
                )

            for consulta in dict.fromkeys(estado.solicitudes_evidencia):
                restante = max_tokens - tokens_evidencia(evidencia, recuperador.tokenizador)
                if restante <= 0:
                    raise _Bloqueo(
                        "EVIDENCE_BUDGET",
                        "No queda presupuesto para ampliar la evidencia solicitada.",
                        JobStatus.REJECTED_QUALITY,
                    )
                nuevos = consultar_multi(consulta, restante)
                if not nuevos:
                    raise _Bloqueo(
                        "COVERAGE_INCOMPLETE",
                        "No se encontró evidencia para una ampliación solicitada por Critic.",
                        JobStatus.REJECTED_QUALITY,
                    )
                for item in nuevos:
                    agregar(item)

            for ronda in range(max((len(p) for p in pools.values()), default=0)):
                for pool in pools.values():
                    ctx.chequear()
                    if ronda < len(pool):
                        agregar(pool[ronda])

            objetivo = list(dict.fromkeys(seccion_del_chunk(e.chunk) for e in evidencia))
        revalidar()
        cfg = recuperador.configuracion
        # registro los metadatos completos de trazabilidad incluyendo modelos utilizados versiones de prompts y configuracion de busqueda
        trazabilidad = Trazabilidad(
            modelo_generacion=cfg.gemini_generation_model,
            modelo_verificacion=cfg.gemini_verification_model,
            modelo_embeddings=recuperador.indice.embeddings.modelo,
            prompt_version=cargar_plantilla(estado.parametros).prompt_version,
            parser_version=dependencias.parser_version,
            retrieval=ConfiguracionRetrieval(
                k=cfg.retrieval_k, fetch_k=cfg.retrieval_fetch_k, lambda_mult=cfg.retrieval_lambda_mult
            ),
        )
        avisos = (
            [f"{len(omitidos - ids)} fragmentos complementarios no caben en el presupuesto de evidencia."]
            if omitidos - ids
            else []
        )
        return update(
            evidencia=evidencia,
            secciones_cubiertas=objetivo,
            trazabilidad=trazabilidad,
            feedback=[*estado.feedback, *avisos],
        )
    except _Bloqueo as error:
        return update(status=error.status, error=DiagnosticoGrafo(code=error.code, message=error.message))
    except TrabajoCanceladoError:
        return update(
            status=JobStatus.CANCELLED,
            error=DiagnosticoGrafo(code="CANCELLED", message="Investigación cancelada o espacio retirado."),
        )
    except DeadlineExcedidoError:
        code, message = "DEADLINE", "La investigación excedió el deadline de ejecución."
    except CuotaAgotadaError:
        code, message = "RATE_LIMITED", "La cuota del modelo de embeddings está agotada."
    except ReintentableError:
        code, message = "PROVIDER_UNAVAILABLE", "El proveedor no pudo completar la recuperación."
    except PermissionError:
        code, message = "NOT_FOUND", "Documento no disponible en este espacio."
    except (IndiceInconsistenteError, ValueError, TypeError):
        code, message = "INVALID_STATE", "El índice, el estado o la evidencia no corresponden a la fuente autorizada."
    except Exception:
        code, message = "INTERNAL", "Un fallo técnico impidió completar la investigación."
    return update(status=JobStatus.FAILED, error=DiagnosticoGrafo(code=code, message=message))
