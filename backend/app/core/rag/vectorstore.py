"""Índice derivado local (#17): Gemini síncrono, publicación completa y aislamiento.

El llamador obtiene workspace/documento autorizados desde la sesión. No se aceptan
filtros libres ni vectores calculados fuera del ContextoEjecucion. La demo tiene
una entrada de lectura aparte; solo la reconstrucción de mantenimiento la escribe.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import chromadb
from chromadb.config import Settings

from app.core.rag.embeddings import GeminiEmbeddings
from app.jobs.manager import ContextoEjecucion, TrabajoCanceladoError
from app.schemas.internal import Chunk
from app.storage.object_keys import clave_documento

DEMO_WORKSPACE_ID = "biblioteca-demo"
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class IndiceInconsistenteError(RuntimeError):
    """El índice derivado está incompleto/incompatible; requiere reconstrucción."""


@dataclass(frozen=True)
class CoincidenciaVectorial:
    chunk: Chunk
    distancia: float
    vector: tuple[float, ...]

    @property
    def similitud(self) -> float:
        """Relevancia coseno acotada; NO es una evaluación de fidelidad."""
        return max(0.0, min(1.0, 1.0 - self.distancia))


@dataclass(frozen=True)
class ResultadoIndexacion:
    document_id: str
    cantidad_chunks: int
    reutilizado: bool


def _huella(chunks: list[Chunk]) -> str:
    # Los IDs/etiquetas cambian entre documentos idénticos. El texto, orden y
    # trazabilidad/configuración deben coincidir antes de copiar sus vectores.
    datos = [c.model_dump(exclude={"chunk_id", "document_id", "workspace_id", "source_name"}) for c in chunks]
    return hashlib.sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _filtro(workspace_id: str, document_id: str, version: str | None = None) -> dict:
    condiciones = [{"workspace_id": workspace_id}, {"document_id": document_id}]
    if version is not None:
        condiciones.append({"version_indice": version})
    return {"$and": condiciones}


class VectorStoreChroma:
    """Un único escritor, ruta dentro de DATA_DIR; catálogo SQLite WAL derivado.

    Un UUID distingue cada intento de indexación. Solo después de confirmar todos
    sus chunks se cambia el puntero activo en SQLite. Cancelar/fallar conserva el
    índice anterior y nunca publica un documento a medias.
    """

    def __init__(self, ruta: str | Path, embeddings: GeminiEmbeddings, *, tamano_lote: int = 64) -> None:
        if type(tamano_lote) is not int or tamano_lote < 1:
            raise ValueError("tamano_lote debe ser un entero positivo")
        self.ruta = Path(ruta).resolve()
        self.ruta.mkdir(parents=True, exist_ok=True)
        self.embeddings = embeddings
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(self.ruta, threading.RLock())
        self._cliente = chromadb.PersistentClient(path=str(self.ruta), settings=Settings(anonymized_telemetry=False))
        self.tamano_lote = min(tamano_lote, self._cliente.get_max_batch_size())
        metadata = {
            "modelo": embeddings.modelo,
            "dimensiones": embeddings.dimensiones,
            "preparacion": embeddings.preparation_version,
            "schema_indice": 1,
        }
        self._privada = self._coleccion(embeddings.collection_name, metadata)
        self._demo = self._coleccion("demo-" + embeddings.collection_name, metadata)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documentos (
                    coleccion TEXT NOT NULL, workspace TEXT NOT NULL, documento TEXT NOT NULL,
                    version TEXT NOT NULL, hash TEXT NOT NULL, firma TEXT NOT NULL, cantidad INTEGER NOT NULL,
                    PRIMARY KEY (coleccion, workspace, documento));
                CREATE TABLE IF NOT EXISTS retirados (
                    workspace TEXT NOT NULL, documento TEXT NOT NULL, PRIMARY KEY (workspace, documento));
            """)

    def _coleccion(self, nombre: str, metadata: dict):
        coleccion = self._cliente.get_or_create_collection(
            nombre,
            embedding_function=None,
            metadata=metadata,
            configuration={"hnsw": {"space": "cosine", "num_threads": 1}},
        )
        if coleccion.metadata != metadata or coleccion.configuration["hnsw"]["space"] != "cosine":
            raise IndiceInconsistenteError("Colección incompatible con modelo, dimensión o preparación")
        return coleccion

    @contextmanager
    def _db(self):
        with self._lock:
            db = sqlite3.connect(self.ruta / "catalogo.sqlite3", timeout=30)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            try:
                with db:
                    yield db
            finally:
                db.close()

    def _autorizar(self, workspace_id: str, document_id: str, contexto: ContextoEjecucion) -> None:
        clave_documento(workspace_id, document_id)  # valida identificadores portables
        if workspace_id == DEMO_WORKSPACE_ID or contexto.workspace_id != workspace_id:
            raise PermissionError("El contexto no autoriza este espacio privado")
        contexto.chequear()

    def _retirado(self, workspace_id: str, document_id: str) -> bool:
        with self._db() as db:
            return (
                db.execute(
                    "SELECT 1 FROM retirados WHERE workspace=? AND documento=?", (workspace_id, document_id)
                ).fetchone()
                is not None
            )

    def _activo(self, coleccion, workspace_id: str, document_id: str):
        with self._db() as db:
            if self._retirado(workspace_id, document_id):
                return None
            return db.execute(
                "SELECT * FROM documentos WHERE coleccion=? AND workspace=? AND documento=?",
                (coleccion.name, workspace_id, document_id),
            ).fetchone()

    def _datos(self, coleccion, activo, *, vectores: bool = False):
        resultado = coleccion.get(
            where=_filtro(activo["workspace"], activo["documento"], activo["version"]),
            include=["metadatas", "embeddings"] if vectores else ["metadatas"],
        )
        if len(resultado["ids"]) != activo["cantidad"]:
            raise IndiceInconsistenteError("Faltan chunks del documento; reconstruir desde su original")
        return resultado

    def contar(self, workspace_id: str, document_id: str, *, contexto: ContextoEjecucion) -> int:
        self._autorizar(workspace_id, document_id, contexto)
        activo = self._activo(self._privada, workspace_id, document_id)
        return len(self._datos(self._privada, activo)["ids"]) if activo else 0

    def indexar(
        self, workspace_id: str, document_id: str, chunks: list[Chunk], *, contexto: ContextoEjecucion
    ) -> ResultadoIndexacion:
        self._autorizar(workspace_id, document_id, contexto)
        return self._indexar(self._privada, workspace_id, document_id, chunks, contexto)

    def _indexar(
        self,
        coleccion,
        workspace_id: str,
        document_id: str,
        chunks: list[Chunk],
        contexto: ContextoEjecucion,
        confirmar: Callable[[], None] = lambda: None,
    ) -> ResultadoIndexacion:
        contexto.chequear()
        if not chunks or len({c.chunk_id for c in chunks}) != len(chunks):
            raise ValueError("Se necesitan chunks con IDs únicos")
        document_hash = chunks[0].document_hash
        if not document_hash or len(document_hash) != 64 or any(c not in "0123456789abcdef" for c in document_hash):
            raise ValueError("Se requiere el SHA-256 del original")
        for chunk in chunks:
            if (chunk.workspace_id, chunk.document_id, chunk.document_hash) != (
                workspace_id,
                document_id,
                document_hash,
            ):
                raise ValueError("Chunk ajeno o de otra versión del documento")
        firma = _huella(chunks)
        if self._retirado(workspace_id, document_id):
            raise TrabajoCanceladoError()
        anterior = self._activo(coleccion, workspace_id, document_id)
        if anterior and anterior["hash"] == document_hash and anterior["firma"] == firma:
            try:
                guardados = self._datos(coleccion, anterior)["metadatas"]
                payloads = [
                    Chunk.model_validate_json(m["chunk"]).model_dump()
                    for m in sorted(guardados, key=lambda m: m["orden"])
                ]
            except (IndiceInconsistenteError, ValueError, KeyError):
                payloads = None  # Reconstruir también repara un índice incompleto.
            if payloads == [c.model_dump() for c in chunks]:
                confirmar()
                contexto.chequear()
                if self._retirado(workspace_id, document_id):
                    raise TrabajoCanceladoError()
                return ResultadoIndexacion(document_id, len(chunks), True)
        # La caché nunca cruza espacios ni colecciones/modelos.
        with self._db() as db:
            fuente = db.execute(
                """SELECT d.* FROM documentos d WHERE coleccion=? AND workspace=? AND hash=? AND firma=?
                   AND NOT EXISTS (SELECT 1 FROM retirados r WHERE r.workspace=d.workspace
                                   AND r.documento=d.documento) LIMIT 1""",
                (coleccion.name, workspace_id, document_hash, firma),
            ).fetchone()
        vectores = None
        if fuente:
            try:
                datos = self._datos(coleccion, fuente, vectores=True)
                orden = sorted(
                    zip(datos["metadatas"], datos["embeddings"], strict=True), key=lambda par: par[0]["orden"]
                )
                vectores = [list(map(float, vector)) for _, vector in orden]
            except IndiceInconsistenteError:
                vectores = None  # Los originales, no el índice roto, son la verdad.
        version = uuid.uuid4().hex
        try:
            for inicio in range(0, len(chunks), self.tamano_lote):
                contexto.chequear()
                if self._retirado(workspace_id, document_id):
                    raise TrabajoCanceladoError()
                lote = chunks[inicio : inicio + self.tamano_lote]
                embeddings = (
                    vectores[inicio : inicio + len(lote)]
                    if vectores is not None
                    else self.embeddings.embed_documents([c.texto for c in lote], contexto=contexto)
                )
                contexto.chequear()
                coleccion.upsert(
                    ids=[
                        hashlib.sha256(f"{workspace_id}:{document_id}:{version}:{c.chunk_id}".encode()).hexdigest()
                        for c in lote
                    ],
                    embeddings=embeddings,
                    documents=[c.texto for c in lote],
                    metadatas=[
                        {
                            "workspace_id": workspace_id,
                            "document_id": document_id,
                            "version_indice": version,
                            "orden": inicio + i,
                            "chunk": c.model_dump_json(),
                        }
                        for i, c in enumerate(lote)
                    ],
                )
            candidato = {
                "workspace": workspace_id,
                "documento": document_id,
                "version": version,
                "cantidad": len(chunks),
            }
            self._datos(coleccion, candidato)
            confirmar()
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                contexto.chequear()
                if self._retirado(workspace_id, document_id):
                    raise TrabajoCanceladoError()
                db.execute(
                    "INSERT OR REPLACE INTO documentos VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (coleccion.name, workspace_id, document_id, version, document_hash, firma, len(chunks)),
                )
        except BaseException as error:
            try:
                coleccion.delete(where=_filtro(workspace_id, document_id, version))
            except Exception:
                error.add_note("Falló la limpieza del intento; sus chunks permanecen invisibles")
            raise
        # El nuevo puntero ya está confirmado. Las versiones anteriores no vuelven
        # a ser visibles; la limpieza se hace sin filtrar recursos de otros espacios.
        coleccion.delete(
            where={"$and": [*_filtro(workspace_id, document_id)["$and"], {"version_indice": {"$ne": version}}]}
        )
        return ResultadoIndexacion(document_id, len(chunks), vectores is not None)

    def borrar(self, workspace_id: str, document_id: str, *, contexto: ContextoEjecucion) -> None:
        self._autorizar(workspace_id, document_id, contexto)
        self._borrar(self._privada, workspace_id, document_id)

    def _borrar(self, coleccion, workspace_id: str, document_id: str, *, definitivo: bool = True) -> None:
        # La lápida se confirma ANTES del borrado físico; una ejecución en vuelo
        # no puede publicar de nuevo. El #19 también debe persistirla en OCI.
        with self._db() as db:
            if definitivo:
                db.execute("INSERT OR IGNORE INTO retirados VALUES (?, ?)", (workspace_id, document_id))
            db.execute("DELETE FROM documentos WHERE workspace=? AND documento=?", (workspace_id, document_id))
        # Retira también versiones de otros modelos/dimensiones del mismo
        # ámbito. Nunca usa un borrado sin ambos identificadores.
        prefijo = "demo-embeddings-" if coleccion.name.startswith("demo-") else "embeddings-"
        for modelo in self._cliente.list_collections():
            if modelo.name.startswith(prefijo):
                self._cliente.get_collection(modelo.name, embedding_function=None).delete(
                    where=_filtro(workspace_id, document_id)
                )

    def buscar(
        self, workspace_id: str, document_id: str, consulta: str, *, contexto: ContextoEjecucion, limite: int = 15
    ) -> list[CoincidenciaVectorial]:
        self._autorizar(workspace_id, document_id, contexto)
        return self._buscar(self._privada, workspace_id, document_id, consulta, contexto, limite)

    def buscar_demo(
        self, document_id: str, consulta: str, *, contexto: ContextoEjecucion, limite: int = 15
    ) -> list[CoincidenciaVectorial]:
        clave_documento(DEMO_WORKSPACE_ID, document_id)
        contexto.chequear()
        return self._buscar(self._demo, DEMO_WORKSPACE_ID, document_id, consulta, contexto, limite)

    def _buscar(self, coleccion, workspace_id, document_id, consulta, contexto, limite):
        if type(limite) is not int or not 1 <= limite <= 100:
            raise ValueError("limite debe estar entre 1 y 100")
        activo = self._activo(coleccion, workspace_id, document_id)
        if activo is None:
            return []  # No consume Gemini por un documento inexistente/ajeno.
        self._datos(coleccion, activo)
        vector = self.embeddings.embed_query(consulta, contexto=contexto)
        contexto.chequear()
        datos = coleccion.query(
            query_embeddings=[vector],
            n_results=min(limite, activo["cantidad"]),
            where=_filtro(workspace_id, document_id, activo["version"]),
            include=["metadatas", "distances", "embeddings"],
        )
        resultado = []
        for metadata, distancia, embedding in zip(
            datos["metadatas"][0], datos["distances"][0], datos["embeddings"][0], strict=True
        ):
            chunk = Chunk.model_validate_json(metadata["chunk"])
            if (chunk.workspace_id, chunk.document_id) != (workspace_id, document_id) or not math.isfinite(distancia):
                raise IndiceInconsistenteError("Trazabilidad o distancia inválida")
            resultado.append(CoincidenciaVectorial(chunk, max(0.0, float(distancia)), tuple(map(float, embedding))))
        contexto.chequear()
        actual = self._activo(coleccion, workspace_id, document_id)
        return resultado if actual and actual["version"] == activo["version"] else []
