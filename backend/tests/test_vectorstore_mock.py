"""Indexación funcional de los embeddings del doble en ChromaDB (issue #10).

Criterio de aceptación 2: "el doble de embeddings produce vectores
estables que Chroma puede indexar (búsqueda funcional en test)". Este test
cierra exactamente eso: indexa textos con los embeddings del doble, busca
por el vector de una consulta y comprueba que el resultado correcto
encabeza la lista. Corre con marca integration_mock (pila completa de
indexación, pero 100% local: Chroma efímero en memoria, sin red).

Chroma se elige efímero (EphemeralClient) y con embedding_function=None:
los vectores llegan YA calculados por el doble, así que nada intenta
descargar modelos por Internet.
"""

import pytest

pytestmark = pytest.mark.integration_mock


async def test_chroma_indexa_y_recupera_con_los_embeddings_del_doble(doble_gemini, chunks_sinteticos):
    chromadb = pytest.importorskip("chromadb", reason="chromadb no instalado en este entorno")

    cliente = chromadb.EphemeralClient()
    coleccion = cliente.get_or_create_collection("test_doble", embedding_function=None)

    # Indexar cada chunk por su texto, con el embedding del doble.
    documentos = [chunk.texto for chunk in chunks_sinteticos]
    vectores = await doble_gemini.embed(documentos)
    coleccion.add(
        ids=[chunk.chunk_id for chunk in chunks_sinteticos],
        documents=documentos,
        embeddings=vectores,
    )
    assert coleccion.count() == len(chunks_sinteticos)

    # Buscar con el embedding de una FRASE del propio chunk: el más similar
    # a sí mismo debe ser el primero (búsqueda funcional, no solo "no explota").
    consulta = (await doble_gemini.embed([documentos[1]]))[0]
    resultado = coleccion.query(query_embeddings=[consulta], n_results=3)
    assert resultado["ids"][0][0] == chunks_sinteticos[1].chunk_id

    # Estabilidad: la MISMA consulta repetida devuelve el mismo orden.
    repetida = coleccion.query(query_embeddings=[consulta], n_results=3)
    assert repetida["ids"] == resultado["ids"]

    cliente.delete_collection("test_doble")
