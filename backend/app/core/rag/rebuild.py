"""Reconstrucción del índice derivado desde originales/manifiestos de OCI (#17).

Sin credenciales propias, SDK alternativo ni reintentos adicionales: recibe el
StorageProvider y contextos del worker. El manifiesto de borrado es autoritativo,
no se deduce que un objeto original remanente deba reaparecer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.rag.chunker import ConfigChunker, trocear
from app.core.rag.parser import LimitesIngesta, parsear_archivo
from app.core.rag.vectorstore import DEMO_WORKSPACE_ID, ResultadoIndexacion, VectorStoreChroma
from app.jobs.manager import ContextoEjecucion, TrabajoCanceladoError
from app.schemas.enums import DocumentStatus
from app.storage.object_keys import clave_demo, clave_documento, clave_original, clave_workspace
from app.storage.provider import StorageNotFound, StorageProvider


class ManifiestoDocumento(BaseModel):
    """Contrato interno v1 que la ingesta #19 persiste junto al original."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    document_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    source_name: str = Field(min_length=1, max_length=255)
    hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    language: Literal["es", "en", "pt"] = "es"
    estado: DocumentStatus = DocumentStatus.PROCESSING
    borrado: bool = False
    parser: LimitesIngesta = Field(default_factory=LimitesIngesta)
    chunker: ConfigChunker = Field(default_factory=ConfigChunker)


class DocumentoReconstruido(BaseModel):
    document_id: str
    cantidad_chunks: int
    requiere_vision: bool
    estado: DocumentStatus
    reutilizado: bool


def _workspace_vigente(storage: StorageProvider, contexto: ContextoEjecucion) -> None:
    contexto.chequear()
    try:
        manifest = json.loads(storage.get(clave_workspace(contexto.workspace_id)))
    except StorageNotFound:
        raise TrabajoCanceladoError() from None
    if manifest.get("workspace_id") != contexto.workspace_id:
        raise ValueError("Manifiesto de espacio ajeno")
    expiracion = datetime.fromisoformat(manifest["expira_en"])
    if expiracion.tzinfo is None:
        raise ValueError("La expiración debe incluir zona horaria")
    if manifest.get("borrado") or expiracion <= datetime.now(timezone.utc):
        raise TrabajoCanceladoError()


def _reconstruir(
    storage: StorageProvider, indice: VectorStoreChroma, contexto: ContextoEjecucion, *, demo: bool, tamano_pagina: int
) -> list[DocumentoReconstruido]:
    if type(tamano_pagina) is not int or not 1 <= tamano_pagina <= 100:
        raise ValueError("tamano_pagina debe estar entre 1 y 100")
    workspace_id = DEMO_WORKSPACE_ID if demo else contexto.workspace_id
    if (contexto.workspace_id == DEMO_WORKSPACE_ID) != demo:
        raise PermissionError("El mantenimiento demo exige su propio contexto")
    prefijo = "demo/" if demo else f"source_documents/{workspace_id}/"
    cursor = None
    vistos: set[str] = set()
    resultado = []
    while True:
        contexto.chequear()
        if not demo:
            _workspace_vigente(storage, contexto)
        pagina = storage.list(prefijo, limit=tamano_pagina, cursor=cursor)
        for objeto in pagina.items:
            partes = objeto.object_name.split("/")
            if not objeto.object_name.startswith(prefijo) or partes[-1] != "manifest.json":
                continue
            manifest = ManifiestoDocumento.model_validate_json(storage.get(objeto.object_name, if_match=objeto.etag))
            document_id = manifest.document_id
            esperado = (
                clave_demo(f"{document_id}/manifest.json") if demo else clave_documento(workspace_id, document_id)
            )
            if objeto.object_name != esperado or manifest.workspace_id != workspace_id or document_id in vistos:
                raise ValueError("Manifiesto ajeno, duplicado o con clave no canónica")
            vistos.add(document_id)
            coleccion = indice._demo if demo else indice._privada
            if manifest.borrado:
                indice._borrar(coleccion, workspace_id, document_id)
                continue
            if indice._retirado(workspace_id, document_id):
                continue
            original = clave_demo(f"{document_id}/original") if demo else clave_original(workspace_id, document_id)
            contenido = storage.get(original)
            if hashlib.sha256(contenido).hexdigest() != manifest.hash_sha256:
                raise ValueError("El original no coincide con su SHA-256")
            contexto.chequear()
            parseo = parsear_archivo(contenido, manifest.source_name, manifest.parser)
            chunks = [
                c.como_chunk_interno()
                for c in trocear(
                    parseo,
                    workspace_id=workspace_id,
                    document_id=document_id,
                    source_name=manifest.source_name,
                    language=manifest.language,
                    config=manifest.chunker,
                )
            ]

            def confirmar(clave=objeto.object_name, etag=objeto.etag):
                contexto.chequear()
                if not demo:
                    _workspace_vigente(storage, contexto)
                # Si cambió/borró el manifiesto durante Gemini no se publica.
                storage.get(clave, if_match=etag)

            if chunks:
                indexado = indice._indexar(coleccion, workspace_id, document_id, chunks, contexto, confirmar)
            elif parseo.requiere_vision:
                confirmar()
                # Escaneo puro: no inventar texto ni reutilizar un índice anterior.
                # Puede completarlo #30 sin cambiar de ID ni revertir una lápida.
                indice._borrar(coleccion, workspace_id, document_id, definitivo=False)
                indexado = ResultadoIndexacion(document_id, 0, False)
            else:
                raise ValueError("El documento no produjo chunks indexables")
            # El #30 reconstruirá las interpretaciones visuales. No reutilizamos
            # un ready anterior para saltarnos la visión del original.
            resultado.append(
                DocumentoReconstruido(
                    document_id=document_id,
                    cantidad_chunks=indexado.cantidad_chunks,
                    requiere_vision=parseo.requiere_vision,
                    estado=parseo.estado_documento,
                    reutilizado=indexado.reutilizado,
                )
            )
        if pagina.next_cursor is None:
            break
        if pagina.next_cursor == cursor:
            raise ValueError("El proveedor repitió el cursor de listado")
        cursor = pagina.next_cursor
    contexto.chequear()
    if not demo:
        _workspace_vigente(storage, contexto)
    coleccion = indice._demo if demo else indice._privada
    # Un manifiesto ausente no autoriza conservar un índice viejo.
    with indice._db() as db:
        anteriores = db.execute(
            "SELECT documento FROM documentos WHERE coleccion=? AND workspace=?", (coleccion.name, workspace_id)
        ).fetchall()
    for anterior in anteriores:
        if anterior["documento"] not in vistos:
            indice._borrar(coleccion, workspace_id, anterior["documento"])
    return resultado


def reconstruir_workspace(
    storage: StorageProvider, indice: VectorStoreChroma, *, contexto: ContextoEjecucion, tamano_pagina: int = 100
) -> list[DocumentoReconstruido]:
    """Reconstruye todos los documentos vivos del espacio autorizado, paginando."""
    return _reconstruir(storage, indice, contexto, demo=False, tamano_pagina=tamano_pagina)


def reconstruir_demo(
    storage: StorageProvider, indice: VectorStoreChroma, *, contexto: ContextoEjecucion, tamano_pagina: int = 100
) -> list[DocumentoReconstruido]:
    """Única escritura de la colección pública: mantenimiento sin endpoint público."""
    return _reconstruir(storage, indice, contexto, demo=True, tamano_pagina=tamano_pagina)


def reconstruir_indice(
    storage: StorageProvider,
    indice: VectorStoreChroma,
    *,
    contextos: Iterable[ContextoEjecucion],
    contexto_demo: ContextoEjecucion | None = None,
) -> list[DocumentoReconstruido]:
    """Recuperación completa: el operador entrega contextos autorizados, en serie.

    Deben compartir el contador central de cuotas y el deadline del trabajo.
    No recupera SQLite operativo, tokens de sesión ni aprobaciones pedagógicas.
    """
    resultado = []
    for contexto in contextos:
        resultado.extend(reconstruir_workspace(storage, indice, contexto=contexto))
    if contexto_demo is not None:
        resultado.extend(reconstruir_demo(storage, indice, contexto=contexto_demo))
    return resultado
