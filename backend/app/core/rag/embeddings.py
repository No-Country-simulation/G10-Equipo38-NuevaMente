"""Cliente de embeddings (issue #13): un vector por chunk, lotes de 16.

- Valida cantidad y dimension de lo devuelto: nunca resultados parciales en silencio.
- Reintenta solo errores transitorios (max 2, backoff exponencial + jitter).
- El proveedor se inyecta: Gemini real o DobleGemini (MOCK_GEMINI=1).
"""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Awaitable, Callable

TAMANO_LOTE = 16
MAX_REINTENTOS = 2
BASE_BACKOFF_SEGUNDOS = 1.0

TAREA_DOCUMENTO = "documento"
TAREA_CONSULTA = "consulta"

# (textos, tarea) -> vectores en el mismo orden
Embedder = Callable[[list[str], str], Awaitable[list[list[float]]]]


class EmbeddingsError(RuntimeError):
    """Error no reintentable del cliente de embeddings."""


class EmbeddingsTransitorioError(EmbeddingsError):
    """Fallo transitorio del proveedor (429, 5xx, timeout): se puede reintentar."""


class EmbeddingsInvalidoError(EmbeddingsError):
    """El proveedor devolvio una respuesta inconsistente (cantidad o dimension)."""


class ClienteEmbeddings:
    def __init__(
        self,
        embedder: Embedder,
        *,
        dimensiones: int = 768,
        tamano_lote: int = TAMANO_LOTE,
        max_reintentos: int = MAX_REINTENTOS,
        dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if dimensiones <= 0 or tamano_lote <= 0 or max_reintentos < 0:
            raise ValueError("dimensiones, tamano_lote y max_reintentos deben ser validos")
        self._embedder = embedder
        self.dimensiones = dimensiones
        self.tamano_lote = tamano_lote
        self.max_reintentos = max_reintentos
        self._dormir = dormir

    async def embed_documents(self, textos: list[str]) -> list[list[float]]:
        """Un vector por texto, mismo orden. Lista vacia -> lista vacia."""
        for i, texto in enumerate(textos):
            if not texto or not texto.strip():
                raise ValueError(f"El texto en la posicion {i} esta vacio; no se puede vectorizar.")
        vectores: list[list[float]] = []
        for inicio in range(0, len(textos), self.tamano_lote):
            lote = textos[inicio : inicio + self.tamano_lote]
            vectores.extend(await self._embed_lote(lote, TAREA_DOCUMENTO))
        return vectores

    async def embed_query(self, texto: str) -> list[float]:
        if not texto or not texto.strip():
            raise ValueError("La consulta esta vacia; no se puede vectorizar.")
        return (await self._embed_lote([texto], TAREA_CONSULTA))[0]

    async def _embed_lote(self, lote: list[str], tarea: str) -> list[list[float]]:
        intento = 0
        while True:
            try:
                vectores = await self._embedder(lote, tarea)
            except EmbeddingsTransitorioError:
                if intento >= self.max_reintentos:
                    raise
                espera = BASE_BACKOFF_SEGUNDOS * (2**intento) + random.uniform(0, 0.5)
                intento += 1
                await self._dormir(espera)
                continue
            self._validar(lote, vectores)
            return vectores

    def _validar(self, lote: list[str], vectores: list[list[float]]) -> None:
        if len(vectores) != len(lote):
            raise EmbeddingsInvalidoError(f"El proveedor devolvio {len(vectores)} vectores para {len(lote)} textos.")
        for i, vector in enumerate(vectores):
            if len(vector) != self.dimensiones:
                raise EmbeddingsInvalidoError(
                    f"El vector {i} tiene {len(vector)} dimensiones; se esperaban {self.dimensiones}."
                )
            if not all(math.isfinite(x) for x in vector):
                raise EmbeddingsInvalidoError(f"El vector {i} contiene valores no finitos.")


def crear_embedder_gemini(api_key: str, modelo: str, dimensiones: int) -> Embedder:
    """Adaptador al SDK google-genai (se importa aqui para no exigirlo en tests)."""
    from google import genai
    from google.genai import errors, types

    cliente = genai.Client(api_key=api_key)
    # Verificar en la doc de gemini-embedding-2 el task_type de cada tarea.
    tareas = {TAREA_DOCUMENTO: "RETRIEVAL_DOCUMENT", TAREA_CONSULTA: "RETRIEVAL_QUERY"}

    async def embedder(textos: list[str], tarea: str) -> list[list[float]]:
        try:
            respuesta = await cliente.aio.models.embed_content(
                model=modelo,
                contents=textos,
                config=types.EmbedContentConfig(output_dimensionality=dimensiones, task_type=tareas[tarea]),
            )
        except errors.APIError as exc:
            if exc.code == 429 or (isinstance(exc.code, int) and exc.code >= 500):
                raise EmbeddingsTransitorioError(str(exc)) from exc
            raise EmbeddingsError(str(exc)) from exc
        except (TimeoutError, ConnectionError) as exc:
            raise EmbeddingsTransitorioError(str(exc)) from exc
        return [list(e.values) for e in respuesta.embeddings]

    return embedder


def crear_cliente_embeddings(configuracion, *, doble=None) -> ClienteEmbeddings:
    """Fabrica: MOCK_GEMINI=1 -> doble inyectado; si no, Gemini real."""
    if configuracion.mock_gemini:
        if doble is None:
            raise EmbeddingsError("MOCK_GEMINI=1 requiere pasar un DobleGemini (solo tests/CI).")

        async def embedder(textos: list[str], tarea: str) -> list[list[float]]:
            return await doble.embed(textos)

    else:
        embedder = crear_embedder_gemini(
            configuracion.google_api_key,
            configuracion.gemini_embedding_model,
            configuracion.embedding_dimensions,
        )
    return ClienteEmbeddings(embedder, dimensiones=configuracion.embedding_dimensions)
