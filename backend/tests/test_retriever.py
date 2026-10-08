"""Chroma nativo; embeddings explícitamente simulados, sin OCI/Gemini reales."""

import time

import pytest
from app.config import Configuracion
from app.core.rag.chunker import trocear
from app.core.rag.embeddings import GeminiEmbeddings
from app.core.rag.parser import parsear_archivo
from app.core.rag.retriever import RetrieverMMR, tokens_evidencia
from app.core.rag.vectorstore import CoincidenciaVectorial, IndiceInconsistenteError, VectorStoreChroma
from app.jobs.manager import (
    ContextoEjecucion,
    CuotaAgotadaError,
    CuotasModelo,
    CuotasProveedor,
    DeadlineExcedidoError,
    ReintentableError,
    TrabajoCanceladoError,
)
from app.schemas.internal import Chunk
from pydantic import ValidationError

pytestmark = pytest.mark.integration_mock
MODELO = "gemini-embedding-2"
HASH = "a" * 64


def contexto(workspace="espacio-a", *, cuotas=None):
    return ContextoEjecucion(
        "job-a",
        workspace,
        "generacion",
        time.monotonic() + 300,
        cuotas=cuotas or CuotasProveedor({MODELO: CuotasModelo(1000, 1000000, 1000)}),
        base_backoff_segundos=0,
    )


def configuracion(**kwargs):
    return Configuracion(_env_file=None, app_env="test", mock_oci=True, mock_gemini=True, **kwargs)


class ProveedorVCN:
    es_mock = True

    def __init__(self):
        self.entradas = []
        self.fallos = 0

    def embed_sync(self, textos, *, timeout):
        self.entradas.append((textos, timeout))
        texto = textos[0]
        if texto.startswith("task:"):
            if self.fallos:
                self.fallos -= 1
                raise ReintentableError("Fallo transitorio simulado")
            return [[1.0, 0.0, 0.0]]
        if "Rutas" in texto:
            return [[1.0, -0.33, 0.0]]
        if "Subredes" in texto:
            return [[1.0, 0.31, 0.0]]
        return [[1.0, 0.3, 0.0]]


def chunk(i, texto, seccion="VCN", *, workspace="espacio-a", documento="doc-a"):
    return Chunk(
        chunk_id=f"{workspace}-{documento}-{i}",
        workspace_id=workspace,
        document_id=documento,
        document_hash=HASH,
        indice=i,
        texto=texto,
        cantidad_tokens=1,
        pagina=i + 1,
        seccion=seccion,
        seccion_id=seccion,
        source_name="redes_vcn_oci.pdf",
        source_type="pdf",
        language="es",
    )


@pytest.fixture
def pila(tmp_path):
    proveedor = ProveedorVCN()
    indice = VectorStoreChroma(tmp_path / "chroma", GeminiEmbeddings(proveedor, modelo=MODELO, dimensiones=3))
    documentos = [
        chunk(0, "NAT permite salida de la subred privada."),
        chunk(1, "Subredes separan redes públicas y privadas."),
        chunk(2, "Rutas determinan el destino del tráfico."),
        chunk(3, "NAT permite salida de la subred privada."),
    ]
    indice.indexar("espacio-a", "doc-a", documentos, contexto=contexto())
    return indice, proveedor, documentos


def test_mmr_diversidad_deduplicacion_y_procedencia(pila):
    indice, _, originales = pila
    retriever = RetrieverMMR(indice, configuracion=configuracion(retrieval_k=2))
    resultado = retriever.recuperar("espacio-a", "doc-a", "VCN", contexto=contexto(), source_hash=HASH)
    assert [e.chunk.chunk_id for e in resultado.evidencia] == [originales[0].chunk_id, originales[2].chunk_id]
    assert all(e.como_referencia().pagina and e.como_referencia().seccion == "VCN" for e in resultado.evidencia)
    assert all(0 <= e.score <= 1 and e.consulta == "VCN" for e in resultado.evidencia)
    assert resultado.tokens == tokens_evidencia(resultado.evidencia, retriever.tokenizador)


def test_presupuesto_cuenta_metadata_y_no_corta_texto(pila):
    indice, _, _ = pila
    retriever = RetrieverMMR(indice, configuracion=configuracion())
    completo = retriever.recuperar("espacio-a", "doc-a", "VCN", contexto=contexto())
    limite = tokens_evidencia(completo.evidencia[:1], retriever.tokenizador)
    resultado = retriever.recuperar("espacio-a", "doc-a", "VCN", contexto=contexto(), presupuesto_tokens=limite)
    assert resultado.evidencia == completo.evidencia[:1]
    assert resultado.tokens <= limite
    assert resultado.recortado and resultado.avisos and resultado.chunks_omitidos
    vacio = retriever.recuperar("espacio-a", "doc-a", "VCN", contexto=contexto(), presupuesto_tokens=1)
    assert vacio.evidencia == [] and vacio.tokens == 0 and vacio.recortado


def test_filtra_seccion_antes_del_limite_de_candidatos(pila):
    indice, proveedor, _ = pila
    nuevos = [chunk(i, "NAT repetida.", "Mayoritaria") for i in range(16)]
    nuevos.append(chunk(16, "Rutas para la sección poco frecuente.", "Minoritaria"))
    indice.indexar("espacio-a", "doc-a", nuevos, contexto=contexto())
    retriever = RetrieverMMR(indice, configuracion=configuracion(retrieval_k=1, retrieval_fetch_k=1))
    resultado = retriever.recuperar("espacio-a", "doc-a", "VCN", contexto=contexto(), seccion="Minoritaria")
    assert [e.chunk.seccion for e in resultado.evidencia] == ["Minoritaria"]
    llamadas = len(proveedor.entradas)
    assert not retriever.recuperar("espacio-a", "doc-a", "VCN", contexto=contexto(), seccion="Ausente").evidencia
    assert len(proveedor.entradas) == llamadas


@pytest.mark.parametrize("consulta", ["red VCN y subredes", "VCN routing and subnets", "rede VCN e sub-redes"])
def test_consultas_multilingues_reutilizan_preparacion_y_contexto(pila, consulta):
    indice, proveedor, _ = pila
    ctx = contexto()
    resultado = RetrieverMMR(indice, configuracion=configuracion()).recuperar(
        "espacio-a",
        "doc-a",
        consulta,
        contexto=ctx,
    )
    assert resultado.evidencia
    assert proveedor.entradas[-1][0] == [f"task: search result | query: {consulta}"]
    assert 0 < proveedor.entradas[-1][1] <= 60


def test_retries_y_cuotas_pertenecen_al_contexto(pila):
    indice, proveedor, _ = pila
    retriever = RetrieverMMR(indice, configuracion=configuracion())
    proveedor.fallos = 1
    previo = len(proveedor.entradas)
    retriever.recuperar("espacio-a", "doc-a", "VCN", contexto=contexto())
    assert len(proveedor.entradas) == previo + 2
    ctx = contexto(cuotas=CuotasProveedor({MODELO: CuotasModelo(1, 100000, 1)}))
    retriever.recuperar("espacio-a", "doc-a", "VCN", contexto=ctx)
    previo = len(proveedor.entradas)
    with pytest.raises(CuotaAgotadaError):
        retriever.recuperar("espacio-a", "doc-a", "VCN", contexto=ctx)
    assert len(proveedor.entradas) == previo


def test_ownership_version_y_presupuesto_cero(pila):
    indice, proveedor, _ = pila
    retriever = RetrieverMMR(indice, configuracion=configuracion())
    previo = len(proveedor.entradas)
    assert not retriever.recuperar("espacio-a", "ajeno", "VCN", contexto=contexto()).evidencia
    with pytest.raises(PermissionError):
        retriever.recuperar("espacio-b", "doc-a", "VCN", contexto=contexto())
    assert len(proveedor.entradas) == previo
    assert not retriever.recuperar("espacio-a", "doc-a", "VCN", contexto=contexto(), presupuesto_tokens=0).evidencia
    assert len(proveedor.entradas) == previo
    with pytest.raises(IndiceInconsistenteError):
        retriever.recuperar("espacio-a", "doc-a", "VCN", contexto=contexto(), source_hash="b" * 64)


@pytest.mark.parametrize(
    "vector,distancia", [((0.0, 0.0, 0.0), 0.1), ((float("nan"), 1.0, 0.0), 0.1), ((1.0, 0.0), float("inf"))]
)
def test_vectores_invalidos_son_fallo_tecnico(pila, monkeypatch, vector, distancia):
    indice, _, originales = pila
    monkeypatch.setattr(indice, "buscar", lambda *a, **kw: [CoincidenciaVectorial(originales[0], distancia, vector)])
    with pytest.raises(IndiceInconsistenteError):
        RetrieverMMR(indice, configuracion=configuracion()).recuperar("espacio-a", "doc-a", "VCN", contexto=contexto())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"retrieval_k": 0},
        {"retrieval_k": 16, "retrieval_fetch_k": 15},
        {"retrieval_lambda_mult": float("nan")},
        {"retrieval_max_tokens": 12001},
    ],
)
def test_configuracion_rechaza_limites_incompatibles(kwargs):
    with pytest.raises(ValidationError):
        configuracion(**kwargs)


def test_configuracion_por_entorno(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_K", "3")
    monkeypatch.setenv("RETRIEVAL_FETCH_K", "9")
    monkeypatch.setenv("RETRIEVAL_LAMBDA_MULT", "0.5")
    monkeypatch.setenv("RETRIEVAL_MAX_TOKENS", "8000")
    cfg = configuracion()
    assert (cfg.retrieval_k, cfg.retrieval_fetch_k, cfg.retrieval_lambda_mult, cfg.retrieval_max_tokens) == (
        3,
        9,
        0.5,
        8000,
    )


def test_pdf_demo_vcn_con_metadatos_citables(pila, documento_demo_vcn):
    indice, _, _ = pila
    parseado = parsear_archivo(documento_demo_vcn.read_bytes(), documento_demo_vcn.name)
    originales = [c.como_chunk_interno() for c in trocear(parseado, "espacio-a", "vcn")]
    indice.indexar("espacio-a", "vcn", originales, contexto=contexto())
    recuperador = RetrieverMMR(indice, configuracion=configuracion())
    resultado = recuperador.recuperar("espacio-a", "vcn", "VCN subredes gateways rutas", contexto=contexto())
    assert 1 < len(resultado.evidencia) <= 5
    assert len({e.chunk.texto for e in resultado.evidencia}) == len(resultado.evidencia)
    assert all(e.chunk.pagina and e.chunk.source_name == documento_demo_vcn.name for e in resultado.evidencia)
    assert resultado.tokens <= 12000
    # Se prueba solo el índice textual, sin declarar el PDF ready ni visión completada.
    pagina = originales[0].pagina
    acotado = recuperador.recuperar(
        "espacio-a",
        "vcn",
        "VCN",
        contexto=contexto(),
        seccion=f"pagina:{pagina}",
    )
    assert acotado.evidencia and all(e.chunk.pagina == pagina for e in acotado.evidencia)


def test_cancelacion_tras_busqueda_y_deadline(pila, monkeypatch):
    indice, _, _ = pila
    recuperador = RetrieverMMR(indice, configuracion=configuracion())
    ctx = contexto()
    buscar = indice.buscar

    def cancelar(*args, **kwargs):
        hits = buscar(*args, **kwargs)
        ctx._evento_cancelacion.set()
        return hits

    monkeypatch.setattr(indice, "buscar", cancelar)
    with pytest.raises(TrabajoCanceladoError):
        recuperador.recuperar("espacio-a", "doc-a", "VCN", contexto=ctx)
    ctx = contexto()
    ctx.deadline = time.monotonic() - 1
    with pytest.raises(DeadlineExcedidoError):
        recuperador.recuperar("espacio-a", "doc-a", "VCN", contexto=ctx)


def test_encabezados_repetidos_tienen_ids_distintos_y_citas_originales(pila):
    indice, _, _ = pila
    texto = "# Configuración\nNAT para salida privada.\n# Configuración\nRutas hacia destinos autorizados.\n"
    # El parser de Markdown conserva la línea del encabezado, no solo el título.
    parseado = parsear_archivo(texto.encode(), "repetidas.md")
    originales = [c.como_chunk_interno() for c in trocear(parseado, "espacio-a", "repetido")]
    assert [c.seccion for c in originales] == ["Configuración", "Configuración"]
    assert [c.seccion_id for c in originales] == ["seccion:1", "seccion:3"]
    assert originales == [c.como_chunk_interno() for c in trocear(parseado, "espacio-a", "repetido")]
    indice.indexar("espacio-a", "repetido", originales, contexto=contexto())
    recuperador = RetrieverMMR(indice, configuracion=configuracion())
    resultado = recuperador.recuperar(
        "espacio-a", "repetido", "Configuración", contexto=contexto(), seccion="seccion:3"
    )
    assert resultado.evidencia and all(e.chunk.seccion_id == "seccion:3" for e in resultado.evidencia)
    assert all(e.como_referencia().seccion == "Configuración" for e in resultado.evidencia)
    assert all("NAT" not in e.chunk.texto for e in resultado.evidencia)


def test_indice_heredado_ambiguo_exige_reconstruccion(pila):
    indice, _, _ = pila
    originales = [chunk(0, "NAT."), chunk(1, "Rutas.")]
    originales = [c.model_copy(update={"pagina": None, "source_type": "md", "seccion_id": None}) for c in originales]
    indice.indexar(
        "espacio-a",
        "heredado",
        [c.model_copy(update={"document_id": "heredado"}) for c in originales],
        contexto=contexto(),
    )
    with pytest.raises(IndiceInconsistenteError, match="reconstruir"):
        RetrieverMMR(indice, configuracion=configuracion()).recuperar(
            "espacio-a", "heredado", "VCN", contexto=contexto(), seccion="VCN"
        )
