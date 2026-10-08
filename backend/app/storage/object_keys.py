"""Claves canónicas de §8.3 y validación portable compartida por OCI/mock."""

import re
from pathlib import PureWindowsPath

from app.storage.provider import StorageInvalidName

BUCKET_PRODUCCION = "nuevamente-contenidos-educativos"


def validar_nombre(object_name: str) -> None:
    if not isinstance(object_name, str) or not object_name or object_name.startswith("/") or "\\" in object_name:
        raise StorageInvalidName("object_name inválido")
    partes = object_name.split("/")
    if partes[0].casefold() == "_meta.json":
        raise StorageInvalidName("Nombre reservado para metadatos internos")
    if any(parte in ("", ".", "..") for parte in partes):
        raise StorageInvalidName("Segmento vacío o relativo")
    for parte in partes:
        if parte.endswith(".") or PureWindowsPath(parte).is_reserved():
            raise StorageInvalidName("Nombre no portable entre Windows y Linux")
        if not all(caracter.isalnum() or caracter in "._-@" for caracter in parte):
            raise StorageInvalidName("Nombre con caracteres no permitidos")


def _id(valor: str) -> str:
    if not isinstance(valor, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", valor) is None:
        raise StorageInvalidName("Usar un identificador generado por el backend")
    return valor


def clave_workspace(workspace_id: str) -> str:
    return f"workspaces/{_id(workspace_id)}/manifest.json"


def clave_original(workspace_id: str, document_id: str) -> str:
    return f"source_documents/{_id(workspace_id)}/{_id(document_id)}/original"


def clave_documento(workspace_id: str, document_id: str) -> str:
    return f"source_documents/{_id(workspace_id)}/{_id(document_id)}/manifest.json"


def clave_output(workspace_id: str, generation_id: str) -> str:
    return f"outputs/{_id(workspace_id)}/{_id(generation_id)}/content.json"


def clave_export(workspace_id: str, generation_id: str, formato: str) -> str:
    if formato not in ("json", "md", "pdf", "csv", "tsv", "apkg"):
        raise StorageInvalidName("Formato de exportación no admitido")
    return f"exports/{_id(workspace_id)}/{_id(generation_id)}/{formato}"


def clave_progress(workspace_id: str) -> str:
    return f"progress/{_id(workspace_id)}/state.json"


def clave_demo(nombre: str) -> str:
    clave = f"demo/{nombre}"
    validar_nombre(clave)
    return clave
