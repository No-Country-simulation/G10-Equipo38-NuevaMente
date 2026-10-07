"""Tests del cliente de embeddings Gemini (issue #13)"""

import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import pytest
from app.config import Configuracion
from app.core.rag.embeddings import GeminiEmbeddings, GoogleGenAIEmbeddingProvider, crear_cliente_embeddings
from app.jobs.manager import ContextoEjecucion, CuotaAgotadaError, CuotasModelo, CuotasProveedor, ReintentableError
from doubles.gemini import DobleGemini
from google.genai.errors import ClientError, ServerError

pytestmark = pytest.mark.integration_mock


@pytest.fixture
def contexto_embeddings() -> ContextoEjecucion:
    return ContextoEjecucion(
        job_id="job-test",
        workspace_id="ws-test",
        tipo="embedding",
        deadline=time.monotonic() + 60,
        cuotas=CuotasProveedor(
            {
                "gemini-embedding-2": CuotasModelo(
                    rpm=200,
                    tpm=1_000_000,
                    rpd=200,
                )
            }
        ),
        reintentos_transitorios=2,
        base_backoff_segundos=0,
    )


def test_cien_documentos_producen_cien_vectores(doble_gemini, contexto_embeddings):
    textos = [f"chunk {i}" for i in range(100)]
    cliente = GeminiEmbeddings(
        proveedor=doble_gemini,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    vectores = cliente.embed_documents(textos, contexto=contexto_embeddings)

    assert len(vectores) == 100
    assert all(len(vector) == 768 for vector in vectores)
    assert doble_gemini.llamadas_embeddings == 100


def test_embed_documents_preserva_el_orden(doble_gemini, contexto_embeddings):
    textos = ["primero", "segundo", "tercero"]
    cliente = GeminiEmbeddings(
        proveedor=doble_gemini,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    vectores = cliente.embed_documents(textos, contexto=contexto_embeddings)

    referencia = DobleGemini(dimension_embeddings=768)
    esperados = [
        (referencia.embed_sync(["title: none | text: primero"], timeout=1))[0],
        (referencia.embed_sync(["title: none | text: segundo"], timeout=1))[0],
        (referencia.embed_sync(["title: none | text: tercero"], timeout=1))[0],
    ]

    assert vectores == esperados


def test_rechaza_vector_con_dimension_incorrecta(contexto_embeddings):
    proveedor = DobleGemini(dimension_embeddings=16)
    cliente = GeminiEmbeddings(
        proveedor=proveedor,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    textos = ["texto"]
    with pytest.raises(ValueError, match="768"):
        cliente.embed_documents(textos, contexto=contexto_embeddings)


def test_embed_query_devuelve_un_vector_de_la_dimension_configurada(doble_gemini, contexto_embeddings):
    cliente = GeminiEmbeddings(
        proveedor=doble_gemini,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    texto = "¿Qué es una VCN?"
    vector = cliente.embed_query(texto, contexto=contexto_embeddings)

    assert len(vector) == 768
    assert doble_gemini.llamadas_embeddings == 1


def test_documento_y_query_usan_preparaciones_distintas(doble_gemini, contexto_embeddings):
    cliente = GeminiEmbeddings(
        proveedor=doble_gemini,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )
    textos = ["Una VCN es una red privada."]
    texto = "¿Qué es una VCN?"
    cliente.embed_documents(textos, contexto=contexto_embeddings)
    cliente.embed_query(texto, contexto=contexto_embeddings)

    texto_documento = doble_gemini.entradas_embeddings[0][0]
    texto_query = doble_gemini.entradas_embeddings[1][0]

    assert texto_documento == "title: none | text: Una VCN es una red privada."
    assert texto_query == "task: search result | query: ¿Qué es una VCN?"


def test_nombre_de_coleccion_cambia_con_modelo_o_dimension(doble_gemini):
    base = GeminiEmbeddings(
        proveedor=doble_gemini,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )
    otro_modelo = GeminiEmbeddings(
        proveedor=doble_gemini,
        modelo="otro-modelo",
        dimensiones=768,
    )
    otra_dimension = GeminiEmbeddings(
        proveedor=doble_gemini,
        modelo="gemini-embedding-2",
        dimensiones=1536,
    )

    assert base.collection_name != otro_modelo.collection_name
    assert base.collection_name != otra_dimension.collection_name


def test_expone_version_de_preparacion(doble_gemini):
    cliente = GeminiEmbeddings(
        proveedor=doble_gemini,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    assert cliente.preparation_version == "asymmetric-retrieval-v1"


def test_rechaza_respuesta_sin_embeddings(contexto_embeddings):
    class ProveedorVacio:
        def embed_sync(self, textos: list[str], *, timeout: float) -> list[list[float]]:
            return []

    cliente = GeminiEmbeddings(
        proveedor=ProveedorVacio(),
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    with pytest.raises(ValueError, match="embedding"):
        textos = ["texto"]
        cliente.embed_documents(textos, contexto=contexto_embeddings)


def test_error_de_cuota_se_propaga_sin_fallback(doble_gemini, contexto_embeddings):
    cliente = GeminiEmbeddings(
        proveedor=doble_gemini,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )
    doble_gemini.programar_error_embeddings(CuotaAgotadaError("Cuota diaria de Gemini agotada."))

    with pytest.raises(CuotaAgotadaError, match="Cuota diaria"):
        textos = ["texto"]
        cliente.embed_documents(textos, contexto=contexto_embeddings)

    assert doble_gemini.llamadas_embeddings == 1


def test_error_transitorio_se_propaga_al_agotar_reintentos(doble_gemini, contexto_embeddings):
    error = ReintentableError("fallo temporal", retry_after=0)

    doble_gemini.programar_error_embeddings(
        error, ReintentableError("fallo temporal", retry_after=0), ReintentableError("fallo temporal", retry_after=0)
    )

    cliente = GeminiEmbeddings(
        proveedor=doble_gemini,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    with pytest.raises(ReintentableError) as capturado:
        cliente.embed_documents(["texto"], contexto=contexto_embeddings)

    assert str(capturado.value) == "fallo temporal"
    assert doble_gemini.llamadas_embeddings == 3


def test_provider_sync_aplica_timeout_y_sin_retries_del_sdk():
    llamadas = []

    class ModelosFake:
        def embed_content(self, **kwargs):
            llamadas.append(kwargs)
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.0] * 768)])

    cliente = SimpleNamespace(models=ModelosFake())

    proveedor = GoogleGenAIEmbeddingProvider(
        cliente=cliente,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    resultado = proveedor.embed_sync(["texto preparado"], timeout=2.5)

    assert len(resultado) == 1
    assert len(resultado[0]) == 768

    assert len(llamadas) == 1
    llamada = llamadas[0]

    assert llamada["model"] == "gemini-embedding-2"
    assert llamada["contents"] == ["texto preparado"]

    config = llamada["config"]
    assert config.output_dimensionality == 768
    assert config.http_options.timeout == 2500
    assert config.http_options.retry_options.attempts == 1


def test_proveedor_google_rechaza_respuesta_sin_embeddings():
    class ModelosFalsos:
        def embed_content(self, **kwargs):
            return SimpleNamespace(embeddings=None)

    cliente_sdk = SimpleNamespace(models=ModelosFalsos())

    proveedor = GoogleGenAIEmbeddingProvider(
        cliente=cliente_sdk,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    with pytest.raises(ValueError, match="embedding"):
        proveedor.embed_sync(["texto preparado"], timeout=1)


def test_proveedor_google_traduce_503_a_reintentable():
    class ModelosFalsos:
        def embed_content(self, **kwargs):
            raise ServerError(
                503, {"error": {"code": 503, "message": "Gemini temporalmente no disponible.", "status": "UNAVAILABLE"}}
            )

    cliente_sdk = SimpleNamespace(models=ModelosFalsos())

    proveedor = GoogleGenAIEmbeddingProvider(
        cliente=cliente_sdk,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    with pytest.raises(ReintentableError, match="temporalmente"):
        proveedor.embed_sync(["texto preparado"], timeout=1)


def test_proveedor_google_traduce_cuota_diaria_a_cuota_agotada():
    class ModelosFalsos:
        def embed_content(self, **kwargs):
            raise ClientError(
                429,
                {
                    "error": {
                        "code": 429,
                        "message": "You exceeded your current quota.",
                        "status": "RESOURCE_EXHAUSTED",
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                "violations": [
                                    {
                                        "quotaMetric": "embedding_requests",
                                        "quotaId": "EmbedRequestsPerDayPerProjectPerModel",
                                    }
                                ],
                            }
                        ],
                    }
                },
            )

    cliente_sdk = SimpleNamespace(models=ModelosFalsos())

    proveedor = GoogleGenAIEmbeddingProvider(
        cliente=cliente_sdk,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    with pytest.raises(CuotaAgotadaError):
        proveedor.embed_sync(["texto preparado"], timeout=1)


def test_proveedor_google_traduce_429_transitorio_con_retry_after():
    class ModelosFalsos:
        def embed_content(self, **kwargs):
            respuesta = httpx.Response(429, headers={"Retry-After": "3"})

            raise ClientError(
                429,
                {
                    "error": {
                        "code": 429,
                        "message": "Rate limit exceeded.",
                        "status": "RESOURCE_EXHAUSTED",
                    }
                },
                response=respuesta,
            )

    cliente_sdk = SimpleNamespace(models=ModelosFalsos())

    proveedor = GoogleGenAIEmbeddingProvider(
        cliente=cliente_sdk,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    with pytest.raises(ReintentableError) as error:
        proveedor.embed_sync(["texto preparado"], timeout=1)

    assert error.value.retry_after == 3.0


def test_proveedor_google_traduce_503_con_retry_after():
    class ModelosFalsos:
        def embed_content(self, **kwargs):
            respuesta = httpx.Response(
                503,
                headers={"Retry-After": "4"},
            )

            raise ServerError(
                503,
                {
                    "error": {
                        "code": 503,
                        "message": "Gemini temporalmente no disponible.",
                        "status": "UNAVAILABLE",
                    }
                },
                response=respuesta,
            )

    cliente_sdk = SimpleNamespace(models=ModelosFalsos())

    proveedor = GoogleGenAIEmbeddingProvider(
        cliente=cliente_sdk,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    with pytest.raises(ReintentableError) as error:
        proveedor.embed_sync(["texto preparado"], timeout=1)

    assert error.value.retry_after == 4.0


def test_proveedor_google_traduce_timeout_a_reintentable():
    class ModelosFalsos:
        def embed_content(self, **kwargs):
            raise httpx.TimeoutException("Gemini no respondio a tiempo.")

    cliente_sdk = SimpleNamespace(models=ModelosFalsos())

    proveedor = GoogleGenAIEmbeddingProvider(
        cliente=cliente_sdk,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    with pytest.raises(ReintentableError, match="tiempo"):
        proveedor.embed_sync(["texto preparado"], timeout=1)


def test_proveedor_google_traduce_error_de_conexion_a_reintentable():
    class ModelosFalsos:
        def embed_content(self, **kwargs):
            request = httpx.Request("POST", "https://generativelanguage.googleapis.com")
            raise httpx.ConnectError(
                "No se pudo conectar con Gemini.",
                request=request,
            )

    cliente_sdk = SimpleNamespace(models=ModelosFalsos())

    proveedor = GoogleGenAIEmbeddingProvider(
        cliente=cliente_sdk,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    with pytest.raises(ReintentableError, match="conectar"):
        proveedor.embed_sync(["texto preparado"], timeout=1)


async def test_rechaza_mas_de_un_embedding_para_un_chunk(contexto_embeddings):
    class ProveedorConRespuestaAmbigua:
        def embed_sync(self, textos: list[str], *, timeout: float) -> list[list[float]]:
            return [
                [0.0] * 768,
                [1.0] * 768,
            ]

    cliente = GeminiEmbeddings(
        proveedor=ProveedorConRespuestaAmbigua(),
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    with pytest.raises(ValueError, match="exactamente un embedding"):
        textos = ["texto"]
        cliente.embed_documents(textos, contexto=contexto_embeddings)


def test_crear_cliente_embeddings_usa_configuracion(doble_gemini: DobleGemini):
    configuracion = Configuracion(
        gemini_embedding_model="modelo-embeddings-test",
        embedding_dimensions=1536,
    )

    cliente = crear_cliente_embeddings(
        configuracion=configuracion,
        proveedor=doble_gemini,
    )

    assert cliente.modelo == "modelo-embeddings-test"
    assert cliente.dimensiones == 1536
    assert cliente.collection_name == "embeddings-modelo-embeddings-test-1536"


def test_mock_gemini_sin_proveedor_explicito_falla():
    configuracion = Configuracion(
        mock_gemini=True,
        gemini_embedding_model="gemini-embedding-2",
        embedding_dimensions=768,
    )

    with pytest.raises(ValueError, match="MOCK_GEMINI"):
        crear_cliente_embeddings(configuracion=configuracion)


def test_crear_cliente_embeddings_construye_proveedor_real(monkeypatch):
    cliente_sdk = object()
    api_keys_recibidas = []

    def cliente_falso(*, api_key):
        api_keys_recibidas.append(api_key)
        return cliente_sdk

    monkeypatch.setattr(
        "app.core.rag.embeddings.genai.Client",
        cliente_falso,
    )

    configuracion = Configuracion(
        mock_gemini=False,
        google_api_key="clave-test",
        gemini_embedding_model="modelo-embeddings-test",
        embedding_dimensions=1536,
    )

    cliente = crear_cliente_embeddings(configuracion=configuracion)

    assert api_keys_recibidas == ["clave-test"]

    assert isinstance(cliente.proveedor, GoogleGenAIEmbeddingProvider)
    assert cliente.proveedor.cliente is cliente_sdk
    assert cliente.proveedor.modelo == "modelo-embeddings-test"
    assert cliente.proveedor.dimensiones == 1536

    assert cliente.modelo == "modelo-embeddings-test"
    assert cliente.dimensiones == 1536


def test_proveedor_google_rechaza_cardinalidad_distinta_a_la_entrada():
    class ModelosFalsos:
        def embed_content(self, **kwargs):
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.0] * 768)])

    cliente_sdk = SimpleNamespace(models=ModelosFalsos())

    proveedor = GoogleGenAIEmbeddingProvider(
        cliente=cliente_sdk,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    with pytest.raises(ValueError, match="cantidad"):
        proveedor.embed_sync(["primero", "segundo"], timeout=1)


def test_embed_documents_reintenta_mediante_contexto(doble_gemini):
    reintentos = []

    cuotas = CuotasProveedor({"gemini-embedding-2": CuotasModelo(rpd=10)})

    contexto = ContextoEjecucion(
        job_id="job-test",
        workspace_id="ws-test",
        tipo="embedding",
        deadline=time.monotonic() + 10,
        cuotas=cuotas,
        reintentos_transitorios=2,
        base_backoff_segundos=0,
        registrar_reintento=lambda: reintentos.append(1),
    )

    doble_gemini.programar_error_embeddings(ReintentableError("fallo temporal"))

    cliente = GeminiEmbeddings(
        proveedor=doble_gemini,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    vectores = cliente.embed_documents(
        ["Una VCN es una red privada."],
        contexto=contexto,
    )

    assert len(vectores) == 1
    assert len(vectores[0]) == 768
    assert doble_gemini.llamadas_embeddings == 2
    assert len(reintentos) == 1
    assert cuotas.disponibles_hoy("gemini-embedding-2") == 8


def test_proveedor_google_traduce_408_a_reintentable():
    class ModelosFake:
        def embed_content(self, **kwargs):
            raise ClientError(408, {"error": {"message": "timeout del proveedor"}})

    proveedor = GoogleGenAIEmbeddingProvider(
        cliente=SimpleNamespace(models=ModelosFake()),
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    with pytest.raises(ReintentableError):
        proveedor.embed_sync(["texto"], timeout=1)


def test_proveedor_google_respeta_retry_after_http_date():
    fecha = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)

    response = httpx.Response(503, headers={"Retry-After": fecha})

    class ModelosFake:
        def embed_content(self, **kwargs):
            raise ServerError(503, {"error": {"message": "temporal"}}, response)

    proveedor = GoogleGenAIEmbeddingProvider(
        cliente=SimpleNamespace(models=ModelosFake()),
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    with pytest.raises(ReintentableError) as capturado:
        proveedor.embed_sync(["texto"], timeout=1)

    assert capturado.value.retry_after is not None
    assert 28 <= capturado.value.retry_after <= 30


def test_proveedor_google_traduce_error_de_transporte_a_reintentable():
    request = httpx.Request("POST", "https://example.com")

    class ModelosFake:
        def embed_content(self, **kwargs):
            raise httpx.ReadError("fallo leyendo respuesta", request=request)

    proveedor = GoogleGenAIEmbeddingProvider(
        cliente=SimpleNamespace(models=ModelosFake()), modelo="gemini-embedding-2", dimensiones=768
    )

    with pytest.raises(ReintentableError):
        proveedor.embed_sync(["texto"], timeout=1)


def test_cien_chunks_generan_cien_solicitudes_individuales_al_sdk(contexto_embeddings):
    llamadas = []

    class ModelosFake:
        def embed_content(self, **kwargs):
            llamadas.append(kwargs)

            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.0] * 768)])

    proveedor = GoogleGenAIEmbeddingProvider(
        cliente=SimpleNamespace(models=ModelosFake()),
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    cliente = GeminiEmbeddings(
        proveedor=proveedor,
        modelo="gemini-embedding-2",
        dimensiones=768,
    )

    textos = [f"chunk {i}" for i in range(100)]

    vectores = cliente.embed_documents(textos, contexto=contexto_embeddings)

    assert len(vectores) == 100
    assert all(len(vector) == 768 for vector in vectores)

    assert len(llamadas) == 100

    for i, llamada in enumerate(llamadas):
        assert llamada["model"] == "gemini-embedding-2"
        assert llamada["contents"] == [f"title: none | text: chunk {i}"]


def test_embed_documents_propaga_timeout_del_contexto():
    timeouts_recibidos = []

    class ProveedorFake:
        def embed_sync(self, textos: list[str], *, timeout: float) -> list[list[float]]:
            timeouts_recibidos.append(timeout)
            return [[0.0] * 768]

    contexto = ContextoEjecucion(
        job_id="job-test",
        workspace_id="ws-test",
        tipo="embedding",
        deadline=time.monotonic() + 10,
        timeout_por_llamada=2.5,
        cuotas=CuotasProveedor({"gemini-embedding-2": CuotasModelo(rpd=10)}),
    )

    cliente = GeminiEmbeddings(proveedor=ProveedorFake(), modelo="gemini-embedding-2", dimensiones=768)

    cliente.embed_documents(["texto"], contexto=contexto)

    assert timeouts_recibidos == [2.5]
