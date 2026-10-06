import asyncio
import math

import pytest
from app.core.rag.embeddings import (
    TAMANO_LOTE,
    ClienteEmbeddings,
    EmbeddingsError,
    EmbeddingsInvalidoError,
    EmbeddingsTransitorioError,
    crear_cliente_embeddings,
)
from doubles.gemini import DobleGemini

pytestmark = pytest.mark.unit


def correr(coro):
    return asyncio.run(coro)


async def _sin_espera(_segundos: float) -> None:
    return None


def cliente_con_doble(**kw) -> ClienteEmbeddings:
    doble = DobleGemini()

    async def embedder(textos, tarea):
        return await doble.embed(textos)

    return ClienteEmbeddings(embedder, dormir=_sin_espera, **kw)


def test_un_vector_por_texto_en_orden_y_normalizado():
    textos = ["VCN", "NAT Gateway", "Subred privada"]
    vectores = correr(cliente_con_doble().embed_documents(textos))
    assert len(vectores) == 3
    assert all(len(v) == 768 for v in vectores)
    assert math.isclose(math.sqrt(sum(x * x for x in vectores[0])), 1.0, rel_tol=1e-6)
    assert vectores[0] != vectores[1]


def test_lista_vacia_devuelve_lista_vacia():
    assert correr(cliente_con_doble().embed_documents([])) == []


def test_lotes_de_16():
    llamadas = []

    async def embedder(textos, tarea):
        llamadas.append(len(textos))
        return [[0.1] * 768 for _ in textos]

    cliente = ClienteEmbeddings(embedder, dormir=_sin_espera)
    resultado = correr(cliente.embed_documents([f"t{i}" for i in range(40)]))
    assert llamadas == [TAMANO_LOTE, TAMANO_LOTE, 8]
    assert len(resultado) == 40


def test_consulta_usa_tarea_consulta():
    tareas = []

    async def embedder(textos, tarea):
        tareas.append(tarea)
        return [[0.1] * 768]

    correr(ClienteEmbeddings(embedder, dormir=_sin_espera).embed_query("que es una VCN"))
    assert tareas == ["consulta"]


def test_texto_vacio_se_rechaza():
    with pytest.raises(ValueError):
        correr(cliente_con_doble().embed_documents(["ok", "   "]))


def test_reintenta_429_y_se_recupera():
    intentos = {"n": 0}
    esperas = []

    async def embedder(textos, tarea):
        intentos["n"] += 1
        if intentos["n"] <= 2:
            raise EmbeddingsTransitorioError("429")
        return [[0.1] * 768 for _ in textos]

    async def dormir(s):
        esperas.append(s)

    resultado = correr(ClienteEmbeddings(embedder, dormir=dormir).embed_documents(["a"]))
    assert len(resultado) == 1 and intentos["n"] == 3
    assert 1.0 <= esperas[0] < 1.5 and 2.0 <= esperas[1] < 2.5  # backoff exponencial + jitter


def test_agota_reintentos_y_propaga():
    intentos = {"n": 0}

    async def embedder(textos, tarea):
        intentos["n"] += 1
        raise EmbeddingsTransitorioError("503")

    with pytest.raises(EmbeddingsTransitorioError):
        correr(ClienteEmbeddings(embedder, dormir=_sin_espera).embed_documents(["a"]))
    assert intentos["n"] == 3  # 1 intento + 2 reintentos


def test_error_permanente_no_se_reintenta():
    intentos = {"n": 0}

    async def embedder(textos, tarea):
        intentos["n"] += 1
        raise EmbeddingsError("400 clave invalida")

    with pytest.raises(EmbeddingsError):
        correr(ClienteEmbeddings(embedder, dormir=_sin_espera).embed_documents(["a"]))
    assert intentos["n"] == 1


def test_respuesta_corta_es_invalida():
    async def embedder(textos, tarea):
        return [[0.1] * 768]

    with pytest.raises(EmbeddingsInvalidoError):
        correr(ClienteEmbeddings(embedder, dormir=_sin_espera).embed_documents(["a", "b"]))


def test_dimension_incorrecta_es_invalida():
    async def embedder(textos, tarea):
        return [[0.1] * 100 for _ in textos]

    with pytest.raises(EmbeddingsInvalidoError):
        correr(ClienteEmbeddings(embedder, dormir=_sin_espera).embed_documents(["a"]))


def test_valores_no_finitos_son_invalidos():
    async def embedder(textos, tarea):
        return [[float("nan")] * 768 for _ in textos]

    with pytest.raises(EmbeddingsInvalidoError):
        correr(ClienteEmbeddings(embedder, dormir=_sin_espera).embed_documents(["a"]))


def test_fabrica_con_mock_usa_el_doble():
    class Cfg:
        mock_gemini = True
        embedding_dimensions = 768

    cliente = crear_cliente_embeddings(Cfg(), doble=DobleGemini())
    assert len(correr(cliente.embed_query("hola"))) == 768


def test_fabrica_con_mock_sin_doble_falla():
    class Cfg:
        mock_gemini = True
        embedding_dimensions = 768

    with pytest.raises(EmbeddingsError):
        crear_cliente_embeddings(Cfg())
