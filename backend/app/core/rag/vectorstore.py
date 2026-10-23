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
from chromadb.errors import NotFoundError

from app.core.rag.embeddings import GeminiEmbeddings
from app.jobs.manager import ContextoEjecucion, TrabajoCanceladoError
from app.schemas.documents import PublicacionDemo
from app.schemas.internal import Chunk
from app.storage.object_keys import clave_documento

DEMO_WORKSPACE_ID = "biblioteca-demo"
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class IndiceInconsistenteError(RuntimeError):
    """El índice derivado está incompleto/incompatible; requiere reconstrucción."""


# con esta estructura inmutable represento una coincidencia hallada en la base vectorial con su distancia y vector numerico
@dataclass(frozen=True)
class CoincidenciaVectorial:
    chunk: Chunk
    distancia: float
    vector: tuple[float, ...]

    # aqui convierto la distancia vectorial a una puntuacion de similitud acotada entre cero y uno donde uno significa maxima coincidencia
    @property
    def similitud(self) -> float:
        """Relevancia coseno acotada; NO es una evaluación de fidelidad."""
        return max(0.0, min(1.0, 1.0 - self.distancia))


@dataclass(frozen=True)
class ResultadoIndexacion:
    document_id: str
    cantidad_chunks: int
    reutilizado: bool


# con esta funcion genero una huella criptografica sha256 del contenido y orden de los fragmentos para detectar si ya estan indexados
def _huella(chunks: list[Chunk]) -> str:
    # Los IDs/etiquetas cambian entre documentos idénticos. El texto, orden y
    # trazabilidad/configuración deben coincidir antes de copiar sus vectores.
    datos = [c.model_dump(exclude={"chunk_id", "document_id", "workspace_id", "source_name"}) for c in chunks]
    return hashlib.sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# aqui construyo el diccionario de filtros que entiende chromadb para aislar las consultas estrictamente por espacio de trabajo y documento
def _filtro(workspace_id: str, document_id: str, version: str | None = None) -> dict:
    condiciones = [{"workspace_id": workspace_id}, {"document_id": document_id}]
    if version is not None:
        condiciones.append({"version_indice": version})
    return {"$and": condiciones}


# con esta funcion obtengo una etiqueta estructural clara para el fragmento ya sea por identificador de seccion o por numero de pagina
def seccion_del_chunk(chunk: Chunk) -> str:
    """ID estructural; una página identifica ubicación, no un tema del PDF."""
    if chunk.seccion_id:
        return chunk.seccion_id
    if chunk.pagina is not None:
        return f"pagina:{chunk.pagina}"
    return chunk.seccion if chunk.seccion and chunk.seccion.strip() else "documento"


# con esta funcion valido que no existan secciones repetidas ambiguas cuando no se dispone de un identificador estructural
def validar_secciones_heredadas(chunks: list[Chunk]) -> None:
    """Sin ID no se pueden distinguir dos encabezados iguales de una sección larga."""
    vistos = set()
    for chunk in chunks:
        if chunk.seccion_id is None and chunk.pagina is None and chunk.seccion:
            if chunk.seccion in vistos:
                raise IndiceInconsistenteError("Secciones sin ID estructural ambiguas; reconstruir desde el original")
            vistos.add(chunk.seccion)


# con esta clase administro el almacenamiento vectorial local en chromadb y su catalogo complementario en sqlite asegurando aislamiento segun el issue 17
class VectorStoreChroma:
    """Un único escritor, ruta dentro de DATA_DIR; catálogo SQLite WAL derivado.

    Un UUID distingue cada intento de indexación. Solo después de confirmar todos
    sus chunks se cambia el puntero activo en SQLite. Cancelar/fallar conserva el
    índice anterior y nunca publica un documento a medias.
    """

    # este metodo estatico me permite consultar rapidamente si un documento fue marcado como retirado sin tener que inicializar el cliente vectorial
    @staticmethod
    def lapidas_persistidas(ruta: str | Path) -> set[tuple[str, str]]:
        """Control sobreviviente de retirada, sin abrir Chroma ni consumir cuotas."""
        archivo = Path(ruta) / "catalogo.sqlite3"
        if not archivo.is_file():
            return set()
        db = sqlite3.connect(archivo.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            return {(ws, doc) for ws, doc in db.execute("SELECT workspace, documento FROM retirados")}
        finally:
            db.close()

    # en el constructor configuro la conexion a chromadb creo el cerrojo de concurrencia y me aseguro de que las tablas en sqlite esten creadas
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

    # con este metodo cierro la conexion persistente de chromadb de manera segura e idempotente
    def cerrar(self) -> None:
        """Libera el cliente persistente, sin borrar ni modificar el índice.

        El dueño del lifespan espera al worker antes de cerrar. Client.close
        administra referencias del System compartido y es idempotente.
        """
        with self._lock:
            self._cliente.close()

    # con este metodo obtengo o creo una coleccion en chromadb validando que su configuracion y distancia coseno sean compatibles
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

    # con este administrador de contexto (contextmanager) abro una conexion a sqlite con modo wal (write ahead logging) para lecturas concurrentes seguras
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

    # con este metodo valido que la peticion tenga permiso sobre el espacio privado y verifico que el trabajo no haya sido cancelado
    def _autorizar(self, workspace_id: str, document_id: str, contexto: ContextoEjecucion) -> None:
        clave_documento(workspace_id, document_id)  # valida identificadores portables
        if workspace_id == DEMO_WORKSPACE_ID or contexto.workspace_id != workspace_id:
            raise PermissionError("El contexto no autoriza este espacio privado")
        contexto.chequear()

    # con este metodo verifico si el documento se encuentra en la tabla de retirados (tombstone) para no devolver informacion eliminada
    def _retirado(self, workspace_id: str, document_id: str) -> bool:
        with self._db() as db:
            return (
                db.execute(
                    "SELECT 1 FROM retirados WHERE workspace=? AND documento=?", (workspace_id, document_id)
                ).fetchone()
                is not None
            )

    # aqui obtengo el registro del documento activo en el catalogo para saber que version exacta de indice debemos consultar
    def _activo(self, coleccion, workspace_id: str, document_id: str):
        with self._db() as db:
            if self._retirado(workspace_id, document_id):
                return None
            return db.execute(
                "SELECT * FROM documentos WHERE coleccion=? AND workspace=? AND documento=?",
                (coleccion.name, workspace_id, document_id),
            ).fetchone()

    # con este metodo recupero los datos almacenados en chromadb verificando que la cantidad de fragmentos coincida con el catalogo
    def _datos(self, coleccion, activo, *, vectores: bool = False):
        resultado = coleccion.get(
            where=_filtro(activo["workspace"], activo["documento"], activo["version"]),
            include=["metadatas", "embeddings"] if vectores else ["metadatas"],
        )
        if len(resultado["ids"]) != activo["cantidad"]:
            raise IndiceInconsistenteError("Faltan chunks del documento; reconstruir desde su original")
        return resultado

    # con este metodo cuento la cantidad de fragmentos que tiene indexados un documento previa validacion de permisos
    def contar(self, workspace_id: str, document_id: str, *, contexto: ContextoEjecucion) -> int:
        self._autorizar(workspace_id, document_id, contexto)
        activo = self._activo(self._privada, workspace_id, document_id)
        return len(self._datos(self._privada, activo)["ids"]) if activo else 0

    # aqui inicio el proceso de indexacion de fragmentos en el espacio privado validando los permisos del contexto
    def indexar(
        self,
        workspace_id: str,
        document_id: str,
        chunks: list[Chunk],
        *,
        contexto: ContextoEjecucion,
        confirmar: Callable[[], None] = lambda: None,
    ) -> ResultadoIndexacion:
        self._autorizar(workspace_id, document_id, contexto)
        return self._indexar(self._privada, workspace_id, document_id, chunks, contexto, confirmar)

    # aqui indexo un lote de fragmentos coordinando la insercion de vectores en chromadb y actualizando el puntero activo en sqlite
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
        # genero un identificador unico universal (uuid) para esta version de indexacion asegurando aislamiento ante ejecuciones concurrentes
        version = uuid.uuid4().hex
        try:
            # con este bucle proceso los fragmentos en lotes calculando sus vectores con gemini e insertandolos en chromadb
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
                            "source_type": c.source_type or "text",
                            "es_diagrama": c.es_diagrama,
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
        contexto.chequear()
        if self._retirado(workspace_id, document_id):
            raise TrabajoCanceladoError()
        return ResultadoIndexacion(document_id, len(chunks), vectores is not None)

    # con este metodo borro un documento del espacio privado eliminando tanto sus vectores como su registro en el catalogo
    def borrar(self, workspace_id: str, document_id: str, *, contexto: ContextoEjecucion) -> None:
        self._autorizar(workspace_id, document_id, contexto)
        self._borrar(self._privada, workspace_id, document_id)

    # aqui elimino fisicamente los registros del documento en sqlite y los fragmentos asociados en todas las colecciones vectoriales
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

    def _retirar_verificado(self, workspace_id: str, document_id: str, *, definitivo: bool = False) -> None:
        """Compensa usando el borrado existente y confirma también chunks físicos."""
        clave_documento(workspace_id, document_id)
        if workspace_id == DEMO_WORKSPACE_ID:
            raise PermissionError("No es un documento privado")
        with self._lock:
            self._borrar(self._privada, workspace_id, document_id, definitivo=definitivo)
            for modelo in self._cliente.list_collections():
                if (
                    modelo.name.startswith("embeddings-")
                    and self._cliente.get_collection(modelo.name, embedding_function=None).get(
                        where=_filtro(workspace_id, document_id), include=[]
                    )["ids"]
                ):
                    raise IndiceInconsistenteError("La retirada física del índice no está confirmada")
            if self._activo(self._privada, workspace_id, document_id) is not None:
                raise IndiceInconsistenteError("El catálogo todavía publica el índice")

    def _retirar_versiones_inactivas_verificado(self, workspace_id: str, document_id: str, previo: dict) -> None:
        """Retira derivados ajenos al puntero conservado sin borrar su catálogo."""
        clave_documento(workspace_id, document_id)
        if workspace_id == DEMO_WORKSPACE_ID:
            raise PermissionError("No es un documento privado")
        with self._lock:
            activo = self._activo(self._privada, workspace_id, document_id)
            if activo is None or dict(activo) != previo:
                raise IndiceInconsistenteError("El índice conservado cambió antes de compensar")
            self._datos(self._privada, activo)
            for modelo in self._cliente.list_collections():
                if not modelo.name.startswith("embeddings-"):
                    continue
                filtro = _filtro(workspace_id, document_id)
                if modelo.name == previo["coleccion"]:
                    filtro["$and"].append({"version_indice": {"$ne": previo["version"]}})
                self._cliente.get_collection(modelo.name, embedding_function=None).delete(where=filtro)
            # Enumerar otra vez también detecta una colección creada durante el barrido.
            for modelo in self._cliente.list_collections():
                if not modelo.name.startswith("embeddings-"):
                    continue
                datos = self._cliente.get_collection(modelo.name, embedding_function=None).get(
                    where=_filtro(workspace_id, document_id), include=["metadatas"]
                )
                if datos["ids"] and (
                    modelo.name != previo["coleccion"]
                    or any(m.get("version_indice") != previo["version"] for m in datos["metadatas"])
                ):
                    raise IndiceInconsistenteError("La retirada de versiones inactivas no está confirmada")
            actual = self._activo(self._privada, workspace_id, document_id)
            if actual is None or dict(actual) != previo:
                raise IndiceInconsistenteError("El índice conservado cambió durante la compensación")
            self._datos(self._privada, actual)

    # con este metodo devuelvo el inventario completo de fragmentos de un documento privado sin necesidad de consultar el modelo de embeddings
    def listar_chunks(
        self,
        workspace_id: str,
        document_id: str,
        *,
        contexto: ContextoEjecucion,
        source_hash: str,
    ) -> list[Chunk]:
        """Inventario de la versión privada autorizada; no consulta Gemini."""
        self._autorizar(workspace_id, document_id, contexto)
        return self._listar_chunks(self._privada, workspace_id, document_id, contexto, source_hash)

    # con este metodo devuelvo el inventario de fragmentos de un documento de demostracion validando la firma de publicacion
    def listar_chunks_demo(
        self,
        document_id: str,
        *,
        contexto: ContextoEjecucion,
        source_hash: str,
        publicacion: PublicacionDemo,
    ) -> list[Chunk]:
        """Lectura demo explícita, sin embeddings ni acceso a colecciones privadas."""
        clave_documento(DEMO_WORKSPACE_ID, document_id)
        contexto.chequear()
        if (publicacion.modelo_embeddings, publicacion.dimensiones_embeddings, publicacion.preparacion_embeddings) != (
            self.embeddings.modelo,
            self.embeddings.dimensiones,
            self.embeddings.preparation_version,
        ):
            raise IndiceInconsistenteError("La publicación demo utiliza otra configuración de embeddings")
        # Revalidar la colección física, incluso si cambió después de abrirla.
        try:
            coleccion = self._cliente.get_collection(self._demo.name, embedding_function=None)
        except NotFoundError:
            return []
        esperada = {
            "modelo": self.embeddings.modelo,
            "dimensiones": self.embeddings.dimensiones,
            "preparacion": self.embeddings.preparation_version,
            "schema_indice": 1,
        }
        if coleccion.metadata != esperada or coleccion.configuration["hnsw"]["space"] != "cosine":
            raise IndiceInconsistenteError("La colección demo dejó de ser compatible")
        return self._listar_chunks(coleccion, DEMO_WORKSPACE_ID, document_id, contexto, source_hash, publicacion)

    def _listar_chunks(self, coleccion, workspace_id, document_id, contexto, source_hash, publicacion=None):
        activo = self._activo(coleccion, workspace_id, document_id)
        if activo is None:
            return []
        if activo["hash"] != source_hash:
            raise IndiceInconsistenteError("El índice no corresponde a la fuente autorizada")
        if publicacion is not None and (
            publicacion.hash_sha256 != source_hash
            or publicacion.version_indice != activo["version"]
            or publicacion.cantidad_chunks != activo["cantidad"]
            or publicacion.huella_chunks != activo["firma"]
        ):
            raise IndiceInconsistenteError("La versión demo no corresponde a su publicación confirmada")
        datos = self._datos(coleccion, activo)
        chunks = [Chunk.model_validate_json(m["chunk"]) for m in sorted(datos["metadatas"], key=lambda m: m["orden"])]
        if any(
            (c.workspace_id, c.document_id, c.document_hash) != (workspace_id, document_id, source_hash) for c in chunks
        ):
            raise IndiceInconsistenteError("El inventario contiene chunks ajenos o de otra versión")
        if publicacion is not None and (
            _huella(chunks) != publicacion.huella_chunks
            or [m["orden"] for m in sorted(datos["metadatas"], key=lambda m: m["orden"])] != list(range(len(chunks)))
            or len({c.chunk_id for c in chunks}) != len(chunks)
            or any(
                ident
                != hashlib.sha256(
                    f"{workspace_id}:{document_id}:{activo['version']}:{m['chunk_id']}".encode()
                ).hexdigest()
                for ident, m in (
                    (i, Chunk.model_validate_json(meta["chunk"]).model_dump())
                    for i, meta in zip(datos["ids"], datos["metadatas"], strict=True)
                )
            )
        ):
            raise IndiceInconsistenteError("El inventario demo no conserva su huella, orden o identidades")
        validar_secciones_heredadas(chunks)
        contexto.chequear()
        actual = self._activo(coleccion, workspace_id, document_id)
        if actual is None or actual["version"] != activo["version"]:
            raise IndiceInconsistenteError("El índice cambió durante la lectura de secciones")
        return chunks

    # con este metodo ejecuto una busqueda vectorial para documentos privados transformando la consulta en vector y buscando los vecinos mas cercanos
    def buscar(
        self,
        workspace_id: str,
        document_id: str,
        consulta: str,
        *,
        contexto: ContextoEjecucion,
        limite: int = 15,
        seccion: str | None = None,
        tipo_fuente: str | None = None,
    ) -> list[CoincidenciaVectorial]:
        self._autorizar(workspace_id, document_id, contexto)
        return self._buscar(self._privada, workspace_id, document_id, consulta, contexto, limite, seccion, tipo_fuente)

    # con este metodo busco documentos dentro de la coleccion de demostracion publica garantizando aislamiento de los documentos privados
    def buscar_demo(
        self,
        document_id: str,
        consulta: str,
        *,
        contexto: ContextoEjecucion,
        limite: int = 15,
        seccion: str | None = None,
        tipo_fuente: str | None = None,
    ) -> list[CoincidenciaVectorial]:
        clave_documento(DEMO_WORKSPACE_ID, document_id)
        contexto.chequear()
        return self._buscar(
            self._demo, DEMO_WORKSPACE_ID, document_id, consulta, contexto, limite, seccion, tipo_fuente
        )

    # en esta funcion interna implemento la logica comun de busqueda aplicando filtros de seccion y tipo de fuente antes de ordenar por similitud
    def _buscar(self, coleccion, workspace_id, document_id, consulta, contexto, limite, seccion, tipo_fuente=None):
        if tipo_fuente not in (None, "pdf", "md", "txt", "text", "image_description"):
            raise ValueError("Tipo de fuente no soportado")
        if type(limite) is not int or not 1 <= limite <= 100:
            raise ValueError("limite debe estar entre 1 y 100")
        activo = self._activo(coleccion, workspace_id, document_id)
        if activo is None:
            return []  # No consume Gemini por un documento inexistente/ajeno.
        datos_activos = self._datos(coleccion, activo)
        filtro = _filtro(workspace_id, document_id, activo["version"])
        cantidad = activo["cantidad"]
        if seccion is not None:
            validar_secciones_heredadas([Chunk.model_validate_json(m["chunk"]) for m in datos_activos["metadatas"]])
            ordenes = [
                m["orden"]
                for m in datos_activos["metadatas"]
                if seccion_del_chunk(Chunk.model_validate_json(m["chunk"])) == seccion
            ]
            if not ordenes:
                return []
            filtro["$and"].append({"orden": {"$in": ordenes}})
            cantidad = len(ordenes)
        if tipo_fuente is not None:
            coincidencias_tipo = []
            for metadata in datos_activos["metadatas"]:
                chunk = Chunk.model_validate_json(metadata["chunk"])
                if (chunk.source_type or "text") == tipo_fuente and (
                    seccion is None or seccion_del_chunk(chunk) == seccion
                ):
                    coincidencias_tipo.append(metadata)
            cantidad = len(coincidencias_tipo)
            if not cantidad:
                return []
            if all(m.get("source_type") == tipo_fuente for m in coincidencias_tipo):
                filtro["$and"].append({"source_type": tipo_fuente})
            else:
                # Los índices anteriores al #30 guardan el tipo en el payload,
                # pero no en metadata. Filtrar sus filas verificadas conserva
                # ámbito/versión sin consumir nuevas indexaciones o vectores.
                filtro["$and"].append({"orden": {"$in": [m["orden"] for m in coincidencias_tipo]}})
        vector = self.embeddings.embed_query(consulta, contexto=contexto)
        contexto.chequear()
        if cantidad <= limite:
            # Se necesitan TODOS los candidatos del ámbito, no una aproximación.
            # HNSW puede omitir vecinos bajo filtros muy selectivos; get conserva
            # espacio/documento/versión/sección/tipo antes de ordenar por coseno.
            exactos = coleccion.get(where=filtro, include=["metadatas", "embeddings"])
            if len(exactos["ids"]) != cantidad:
                raise IndiceInconsistenteError("Faltan candidatos del ámbito filtrado")
            norma_query = math.hypot(*vector)
            if not norma_query or not math.isfinite(norma_query):
                raise IndiceInconsistenteError("El vector de consulta no permite comparar por coseno")
            filas = []
            for metadata, embedding in zip(exactos["metadatas"], exactos["embeddings"], strict=True):
                contexto.chequear()
                norma = math.hypot(*embedding)
                if len(embedding) != len(vector) or not norma or not math.isfinite(norma):
                    raise IndiceInconsistenteError("El índice contiene un vector incompatible")
                distancia = 1.0 - sum(
                    (float(a) / norma_query) * (float(b) / norma) for a, b in zip(vector, embedding, strict=True)
                )
                filas.append((metadata, max(0.0, min(2.0, distancia)), embedding))
            filas.sort(key=lambda f: (f[1], f[0]["orden"]))
            datos = {
                "metadatas": [[f[0] for f in filas]],
                "distances": [[f[1] for f in filas]],
                "embeddings": [[f[2] for f in filas]],
            }
        else:
            datos = coleccion.query(
                query_embeddings=[vector],
                n_results=limite,
                where=filtro,
                include=["metadatas", "distances", "embeddings"],
            )
        resultado = []
        for metadata, distancia, embedding in zip(
            datos["metadatas"][0], datos["distances"][0], datos["embeddings"][0], strict=True
        ):
            chunk = Chunk.model_validate_json(metadata["chunk"])
            if tipo_fuente is not None and (chunk.source_type or "text") != tipo_fuente:
                raise IndiceInconsistenteError("El tipo de fuente no corresponde al filtro")
            if (chunk.workspace_id, chunk.document_id) != (workspace_id, document_id) or not math.isfinite(distancia):
                raise IndiceInconsistenteError("Trazabilidad o distancia inválida")
            resultado.append(CoincidenciaVectorial(chunk, max(0.0, float(distancia)), tuple(map(float, embedding))))
        contexto.chequear()
        actual = self._activo(coleccion, workspace_id, document_id)
        return resultado if actual and actual["version"] == activo["version"] else []

    def buscar_multidocumental(
        self,
        workspace_id: str,
        document_ids: list[str],
        consulta: str,
        *,
        contexto: ContextoEjecucion,
        limite: int = 15,
        tipo_fuente: str | None = None,
    ) -> list[CoincidenciaVectorial]:
        """Búsqueda vectorial sobre un conjunto de documentos autorizados del espacio.

        Aplica un filtro compuesto por la versión activa de cada documento para evitar
        residuos o chunks zombi de versiones previas.
        """
        if not document_ids:
            return []
        if tipo_fuente not in (None, "pdf", "md", "txt", "text", "image_description"):
            raise ValueError("Tipo de fuente no soportado")
        if type(limite) is not int or not 1 <= limite <= 100:
            raise ValueError("limite debe estar entre 1 y 100")

        for doc_id in document_ids:
            self._autorizar(workspace_id, doc_id, contexto)

        activos = {}
        for doc_id in document_ids:
            act = self._activo(self._privada, workspace_id, doc_id)
            if act is not None:
                activos[doc_id] = act

        if not activos:
            return []

        clausulas = []
        for doc_id, act in activos.items():
            conds = [
                {"workspace_id": workspace_id},
                {"document_id": doc_id},
                {"version_indice": act["version"]},
            ]
            if tipo_fuente is not None:
                conds.append({"source_type": tipo_fuente})
            clausulas.append({"$and": conds})

        filtro = clausulas[0] if len(clausulas) == 1 else {"$or": clausulas}

        total_candidatos = sum(act["cantidad"] for act in activos.values())
        vector = self.embeddings.embed_query(consulta, contexto=contexto)
        contexto.chequear()

        if total_candidatos <= limite:
            exactos = self._privada.get(where=filtro, include=["metadatas", "embeddings"])
            if not exactos["ids"]:
                return []
            norma_query = math.hypot(*vector)
            if not norma_query or not math.isfinite(norma_query):
                raise IndiceInconsistenteError("El vector de consulta no permite comparar por coseno")
            filas = []
            for metadata, embedding in zip(exactos["metadatas"], exactos["embeddings"], strict=True):
                contexto.chequear()
                norma = math.hypot(*embedding)
                if len(embedding) != len(vector) or not norma or not math.isfinite(norma):
                    raise IndiceInconsistenteError("El índice contiene un vector incompatible")
                distancia = 1.0 - sum(
                    (float(a) / norma_query) * (float(b) / norma) for a, b in zip(vector, embedding, strict=True)
                )
                filas.append((metadata, max(0.0, min(2.0, distancia)), embedding))
            filas.sort(key=lambda f: (f[1], f[0].get("orden", 0)))
            datos = {
                "metadatas": [[f[0] for f in filas]],
                "distances": [[f[1] for f in filas]],
                "embeddings": [[f[2] for f in filas]],
            }
        else:
            datos = self._privada.query(
                query_embeddings=[vector],
                n_results=limite,
                where=filtro,
                include=["metadatas", "distances", "embeddings"],
            )

        resultado = []
        if datos["metadatas"] and datos["metadatas"][0]:
            for metadata, distancia, embedding in zip(
                datos["metadatas"][0], datos["distances"][0], datos["embeddings"][0], strict=True
            ):
                chunk = Chunk.model_validate_json(metadata["chunk"])
                if tipo_fuente is not None and (chunk.source_type or "text") != tipo_fuente:
                    raise IndiceInconsistenteError("El tipo de fuente no corresponde al filtro")
                if (
                    chunk.workspace_id != workspace_id
                    or chunk.document_id not in activos
                    or not math.isfinite(distancia)
                ):
                    raise IndiceInconsistenteError("Trazabilidad o distancia inválida")
                resultado.append(CoincidenciaVectorial(chunk, max(0.0, float(distancia)), tuple(map(float, embedding))))

        contexto.chequear()
        for doc_id, act in activos.items():
            actual = self._activo(self._privada, workspace_id, doc_id)
            if actual is None or actual["version"] != act["version"]:
                return []
        return resultado
