"""Researcher con doble de retrieval y Chroma nativo, sin proveedores reales."""

import time
from types import SimpleNamespace

import pytest
from app.config import Configuracion
from app.core.agents.graph_state import DependenciasGrafo, DocumentoGeneracion, EstadoGrafo
from app.core.agents.researcher import DependenciasResearcher, researcher
from app.core.agents.supervisor import crear_estado_inicial
from app.core.rag.chunker import trocear
from app.core.rag.embeddings import GeminiEmbeddings
from app.core.rag.parser import parsear_archivo
from app.core.rag.retriever import ResultadoRecuperacion, RetrieverMMR, tokens_evidencia
from app.core.rag.tokenizer import TokenizadorBPE
from app.core.rag.vectorstore import IndiceInconsistenteError, VectorStoreChroma, seccion_del_chunk
from app.jobs.manager import (
    ContextoEjecucion,
    CuotaAgotadaError,
    CuotasModelo,
    CuotasProveedor,
    ReintentableError,
    TrabajoCanceladoError,
)
from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.internal import Chunk, EvidenciaRecuperada
from app.schemas.responses import DocumentoFuente
from doubles.gemini import DobleGemini

pytestmark = pytest.mark.integration_mock
HASH = "a" * 64
SOLICITUD = {
    "document_id": "doc-a",
    "perfil_destinatario": "principiante",
    "formato_salida": "flashcards",
    "nicho_sector": "general",
    "nivel_detalle": "didactico",
    "idioma_salida": "pt",
}


def fragmentos():
    return [
        Chunk(
            chunk_id=f"chunk-{i}",
            workspace_id="espacio-a",
            document_id="doc-a",
            document_hash=HASH,
            indice=i,
            texto=texto,
            seccion=titulo,
            seccion_id=f"seccion:{i + 1}",
            cantidad_tokens=1,
            source_name="redes_vcn_oci.md",
            source_type="md",
            linea_inicio=i + 1,
            linea_fin=i + 1,
        )
        for i, (titulo, texto) in enumerate(
            [
                ("VCN", "Una VCN es una red virtual privada en OCI."),
                ("Subredes", "Las subredes dividen una VCN en rangos de direcciones."),
                ("Rutas", "Las tablas de rutas determinan el destino del tráfico."),
            ]
        )
    ]


class IndiceDoble:
    embeddings = SimpleNamespace(modelo="gemini-embedding-2", dimensiones=3)

    def __init__(self, chunks):
        self.chunks = chunks

    def listar_chunks(self, workspace, document, *, contexto, source_hash):
        contexto.chequear()
        assert (
            workspace == contexto.workspace_id and document == "doc-a" and source_hash == self.chunks[0].document_hash
        )
        return self.chunks


class RetrievalDoble:
    def __init__(self, chunks):
        self.indice = IndiceDoble(chunks)
        self.configuracion = Configuracion(_env_file=None, app_env="test", mock_oci=True, mock_gemini=True)
        self.tokenizador = TokenizadorBPE()
        self.llamadas = []
        self.error = None
        self.vacio_primero = False
        self.despues = lambda: None

    @property
    def max_tokens(self):
        return self.configuracion.retrieval_max_tokens

    def recuperar(self, workspace, document, consulta, **kwargs):
        self.llamadas.append((consulta, kwargs))
        if self.error:
            raise self.error
        evidencia = [
            EvidenciaRecuperada(chunk=c, score=0.9, consulta=consulta)
            for c in self.indice.chunks
            if kwargs["seccion"] is None or seccion_del_chunk(c) == kwargs["seccion"]
        ]
        if self.vacio_primero and len(self.llamadas) == 1:
            evidencia = []
        self.despues()
        # El nodo debe contar la unión real incluso si el proveedor informa mal los tokens.
        return ResultadoRecuperacion(evidencia=evidencia, tokens=0)


@pytest.fixture
def entorno():
    chunks = fragmentos()
    ctx = ContextoEjecucion(
        "job-a",
        "espacio-a",
        "generacion",
        time.monotonic() + 300,
        cuotas=CuotasProveedor({"gemini-embedding-2": CuotasModelo(1000, 1000000, 1000)}),
        base_backoff_segundos=0,
    )
    doc = DocumentoGeneracion(
        workspace_id="espacio-a",
        fuente=DocumentoFuente(document_id="doc-a", titulo="Redes VCN en OCI", hash=HASH, version="v1"),
        estado=DocumentStatus.READY,
        idioma_origen="en",
        secciones=tuple(seccion_del_chunk(c) for c in chunks),
    )
    grafo = DependenciasGrafo(ctx, lambda *_: doc)
    recuperador = RetrievalDoble(chunks)
    dependencias = DependenciasResearcher(grafo, recuperador, parser_version="parser-fixture-v1")
    estado = crear_estado_inicial(SOLICITUD, generation_id="gen-a", dependencias=grafo)
    return estado, dependencias, doc


def validar_update(estado, update):
    return EstadoGrafo.model_validate({**estado.model_dump(), **update})


def test_cubre_todas_las_secciones_vcn_sin_mutar_ni_redactar(entorno, monkeypatch):
    estado, dep, _ = entorno
    previo = estado.model_dump_json()
    monkeypatch.setattr(
        dep.grafo.ejecucion, "llamar", lambda *_a, **_kw: pytest.fail("La planificación es determinista")
    )
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.status == JobStatus.RUNNING and resultado.borrador is None
    assert resultado.secciones_cubiertas == list(estado.secciones_disponibles)
    assert len(resultado.evidencia) == 3 and len({e.chunk.chunk_id for e in resultado.evidencia}) == 3
    assert resultado.presupuesto.usadas == estado.presupuesto.usadas == 0 and resultado.intento == 0
    assert resultado.trazabilidad.parser_version == "parser-fixture-v1"
    assert estado.model_dump_json() == previo
    assert all(kw["contexto"] is dep.grafo.ejecucion and kw["source_hash"] == HASH for _, kw in dep.retriever.llamadas)
    assert "definitions" in dep.retriever.llamadas[0][0]  # Consulta en idioma de fuente; salida sigue PT.
    assert resultado.parametros.idioma_salida.value == "pt"


def test_alcance_especifico_no_mezcla_secciones(entorno):
    _, dep, _ = entorno
    estado = crear_estado_inicial(
        {**SOLICITUD, "alcance": {"tipo": "seccion", "seccion_id": "seccion:2"}},
        generation_id="gen-a",
        dependencias=dep.grafo,
    )
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.status == JobStatus.RUNNING and resultado.secciones_cubiertas == ["seccion:2"]
    assert all(e.chunk.seccion_id == "seccion:2" for e in resultado.evidencia)
    assert len(dep.retriever.llamadas) == 1


def test_unico_presupuesto_para_toda_la_union_y_diagnostico(entorno):
    estado, dep, _ = entorno
    elementos = [EvidenciaRecuperada(chunk=c, score=0.9, consulta="x") for c in fragmentos()]
    limite = tokens_evidencia(elementos[:2], dep.retriever.tokenizador) - 1
    dep.retriever.configuracion.retrieval_max_tokens = limite
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.status == JobStatus.REJECTED_QUALITY and resultado.error.code == "EVIDENCE_BUDGET"
    assert "seccion:" in resultado.error.message and "Acotá" in resultado.error.message
    assert resultado.evidencia == [] and resultado.borrador is None and resultado.trazabilidad is None
    assert all(kw["presupuesto_tokens"] <= limite for _, kw in dep.retriever.llamadas)


def test_amplia_consulta_vacia_y_no_consume_redacciones(entorno):
    estado, dep, _ = entorno
    dep.retriever.vacio_primero = True
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.status == JobStatus.RUNNING and len(dep.retriever.llamadas) == 4
    assert dep.retriever.llamadas[0][0] != dep.retriever.llamadas[1][0]
    assert resultado.intento == 0


def test_reentrada_de_critic_reutiliza_evidencia_y_atiende_solicitud(entorno):
    estado, dep, _ = entorno
    preparado = validar_update(estado, researcher(estado, dep))
    preparado = preparado.model_copy(
        update={"intento": 1, "solicitudes_evidencia": ["explicar tabla de rutas"], "feedback": ["Precisar las rutas"]}
    )
    dep.retriever.llamadas.clear()
    resultado = validar_update(preparado, researcher(preparado, dep))
    assert len(dep.retriever.llamadas) == 1 and dep.retriever.llamadas[0][0] == "explicar tabla de rutas"
    assert resultado.intento == 1 and "Precisar las rutas" in resultado.feedback
    assert resultado.solicitudes_evidencia == []


@pytest.mark.parametrize(
    "error,code,status",
    [
        (CuotaAgotadaError("secreto"), "RATE_LIMITED", JobStatus.FAILED),
        (ReintentableError("secreto"), "PROVIDER_UNAVAILABLE", JobStatus.FAILED),
        (TrabajoCanceladoError(), "CANCELLED", JobStatus.CANCELLED),
        (RuntimeError("secreto"), "INTERNAL", JobStatus.FAILED),
        (IndiceInconsistenteError("secreto"), "INVALID_STATE", JobStatus.FAILED),
    ],
)
def test_fallo_tecnico_no_expone_evidencia_ni_secretos(entorno, error, code, status):
    estado, dep, _ = entorno
    dep.retriever.error = error
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.status == status and resultado.error.code == code
    assert resultado.evidencia == [] and resultado.borrador is None
    assert "secreto" not in resultado.model_dump_json()
    assert len(dep.retriever.llamadas) == 1  # No repite el pipeline por fallo transitorio.


def test_fuente_cambiada_en_vuelo_descarta_resultado(entorno):
    estado, dep, doc = entorno
    dep.retriever.despues = lambda: setattr(doc.fuente, "hash", "b" * 64)
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.status == JobStatus.FAILED and resultado.error.code == "INVALID_STATE"
    assert not resultado.evidencia


def test_vision_pendiente_y_ownership_se_validan_antes_de_buscar(entorno):
    estado, dep, doc = entorno
    doc.vision_pendiente = True
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.error.code == "INVALID_STATE" and dep.retriever.llamadas == []
    doc.vision_pendiente = False
    estado.workspace_id = "otro-espacio"
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.error.code == "NOT_FOUND" and dep.retriever.llamadas == []


def test_encabezados_repetidos_se_cubren_por_id_y_no_por_titulo(entorno):
    estado, dep, _ = entorno
    dep.retriever.indice.chunks = [c.model_copy(update={"seccion": "Configuración"}) for c in fragmentos()]
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.status == JobStatus.RUNNING
    assert resultado.secciones_cubiertas == ["seccion:1", "seccion:2", "seccion:3"]
    assert len(resultado.evidencia) == 3


def test_deadline_y_limite_de_redacciones_no_hacen_consultas(entorno):
    estado, dep, _ = entorno
    estado.intento = 3
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.status == JobStatus.REJECTED_QUALITY and resultado.error.code == "MAX_ATTEMPTS"
    estado.intento = 0
    dep.grafo.ejecucion.deadline = time.monotonic() - 1
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.status == JobStatus.FAILED and resultado.error.code == "DEADLINE"
    assert dep.retriever.llamadas == []


def test_researcher_con_chroma_y_parser_md_nativos(tmp_path):
    texto = "# Configuración\nNAT permite salida privada.\n# Configuración\nLas rutas eligen destinos.\n# Subredes\nLas subredes delimitan direcciones.\n"
    parseado = parsear_archivo(texto.encode(), "vcn.md")
    chunks = [c.como_chunk_interno() for c in trocear(parseado, "espacio-a", "doc-a")]
    doble = DobleGemini(dimension_embeddings=16)
    ctx = ContextoEjecucion(
        "job-a",
        "espacio-a",
        "generacion",
        time.monotonic() + 300,
        cuotas=CuotasProveedor({"gemini-embedding-2": CuotasModelo(1000, 1000000, 1000)}),
    )
    indice = VectorStoreChroma(
        tmp_path / "chroma", GeminiEmbeddings(doble, modelo="gemini-embedding-2", dimensiones=16)
    )
    indice.indexar("espacio-a", "doc-a", chunks, contexto=ctx)
    doc = DocumentoGeneracion(
        workspace_id="espacio-a",
        fuente=DocumentoFuente(document_id="doc-a", titulo="VCN", hash=parseado.hash_sha256, version="v1"),
        estado=DocumentStatus.READY,
        idioma_origen="es",
        secciones=tuple(dict.fromkeys(c.seccion_id for c in chunks)),
    )
    grafo = DependenciasGrafo(ctx, lambda *_: doc)
    cfg = Configuracion(_env_file=None, app_env="test", mock_oci=True, mock_gemini=True)
    dep = DependenciasResearcher(grafo, RetrieverMMR(indice, configuracion=cfg), parser_version="parser-fixture-v1")
    estado = crear_estado_inicial(SOLICITUD, generation_id="gen-a", dependencias=grafo)
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.status == JobStatus.RUNNING and resultado.secciones_cubiertas == list(doc.secciones)
    assert tokens_evidencia(resultado.evidencia, dep.retriever.tokenizador) <= dep.retriever.max_tokens
    assert doble.llamadas_embeddings == len(chunks) + len(doc.secciones)
    assert resultado.presupuesto.usadas == 0


def test_cobertura_pdf_vcn_con_estado_ready_explicito_del_doble(entorno, documento_demo_vcn):
    _, _, _ = entorno
    parseado = parsear_archivo(documento_demo_vcn.read_bytes(), documento_demo_vcn.name)
    chunks = [c.como_chunk_interno() for c in trocear(parseado, "espacio-a", "doc-a")]
    recuperador = RetrievalDoble(chunks)
    ctx = ContextoEjecucion("job-a", "espacio-a", "generacion", time.monotonic() + 300)
    # Solo se prueba el nodo con metadata ready simulada. La ingestión real del
    # PDF sigue esperando visión (#30); este doble no la declara implementada.
    doc = DocumentoGeneracion(
        workspace_id="espacio-a",
        fuente=DocumentoFuente(document_id="doc-a", titulo="VCN demo", hash=parseado.hash_sha256, version="v1"),
        estado=DocumentStatus.READY,
        idioma_origen="es",
        secciones=tuple(dict.fromkeys(seccion_del_chunk(c) for c in chunks)),
    )
    grafo = DependenciasGrafo(ctx, lambda *_: doc)
    dep = DependenciasResearcher(grafo, recuperador, parser_version="parser-fixture-v1")
    estado = crear_estado_inicial(SOLICITUD, generation_id="gen-a", dependencias=grafo)
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.status == JobStatus.RUNNING
    assert resultado.secciones_cubiertas == list(doc.secciones)
    assert {seccion_del_chunk(e.chunk) for e in resultado.evidencia} == set(doc.secciones)
    assert tokens_evidencia(resultado.evidencia, recuperador.tokenizador) <= 12000
    assert all(id.startswith("pagina:") for id in doc.secciones)  # Ubicación, no tema inferido.


@pytest.mark.parametrize(
    "idioma,perfil,formato,termino",
    [
        ("es", "junior_ssr", "tutorial", "configuración"),
        ("en", "lider_tecnico", "quiz", "constraints"),
        ("pt", "ejecutivo", "resumen_ejecutivo", "riscos"),
        ("es", "principiante", "guion_clase", "secuencia"),
    ],
)
def test_consulta_se_adapta_a_fuente_formato_y_perfil(entorno, idioma, perfil, formato, termino):
    _, dep, doc = entorno
    doc.idioma_origen = idioma
    estado = crear_estado_inicial(
        {**SOLICITUD, "perfil_destinatario": perfil, "formato_salida": formato},
        generation_id="gen-a",
        dependencias=dep.grafo,
    )
    resultado = validar_update(estado, researcher(estado, dep))
    assert resultado.status == JobStatus.RUNNING
    assert termino in dep.retriever.llamadas[0][0]
