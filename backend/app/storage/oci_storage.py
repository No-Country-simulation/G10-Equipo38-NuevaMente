"""Proveedores de almacenamiento: mock local (este issue) y factory (issue #04).

Aquí viven DOS cosas, y es fácil confundirlas:

1. `LocalMockStorageProvider`: la implementación de `StorageProvider` que
   guarda archivos locales bajo `.data/oci_mock_storage/`. Es la única que
   existe HOY; imita la semántica de OCI Object Storage (ETag, creación y
   actualización condicionales, paginación) usando solo la biblioteca
   estándar de Python, para que desarrollo y CI corran sin red, sin
   credenciales y sin costo.

2. `get_storage_provider()`: la FÁBRICA que decide qué proveedor usar
   leyendo la variable de entorno MOCK_OCI. Regla de oro de §8.2:

       MOCK_OCI=1  -> mock (solo desarrollo/CI).
       MOCK_OCI=0  -> proveedor real; si faltan credenciales, ERROR DE
                      ARRANQUE con mensaje accionable. NUNCA fallback al
                      mock: una falla real convertida en "éxito local" es
                      exactamente el falso positivo que §12.2 (criterio 10)
                      prohíbe demostrar.

   El proveedor OCI REAL llega con el issue #14; hasta entonces, pedir modo
   real falla en el arranque con StorageConfigError explicando qué falta.

Cómo el mock imita a OCI, para quien lo lea después:

- Un `object_name` como "outputs/ws_1/gen_1/content.json" es un ARCHIVO en
  esa misma ruta relativa dentro del directorio base del mock. Los
  prefijos de §8.3 funcionan idéntico que en producción.
- El ETag es el hash SHA-256 del contenido: determinista (mismo contenido
  => misma huella) y suficiente para detectar escrituras concurrentes, que
  es lo que el control de versión de §7.2 necesita.
- Los metadatos (content_type, última modificación) viven en un índice
  `_meta.json` dentro del directorio base, aparte de los contenidos: así
  el "listado" no depende de estadísticas del sistema de archivos, que
  difieren entre Windows y Linux.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path, PureWindowsPath
from tempfile import NamedTemporaryFile

from app.storage.provider import (
    ListedObject,
    StorageConfigError,
    StorageConflict,
    StorageInvalidName,
    StorageNotFound,
    StoragePage,
    StorageProvider,
    StoredObject,
)

# El bucket de producción como constante única (§8.1): el mock replica sus
# prefijos, y el proveedor real (#14) usará este mismo nombre.
BUCKET_PRODUCCION = "nuevamente-contenidos-educativos"

# Variables que exigirá el proveedor real (issue #14), según el Apéndice A.
VARIABLES_REQUERIDAS_REAL = ("OCI_BUCKET_NAME", "OCI_COMPARTMENT_ID", "OCI_REGION", "OCI_CONFIG_FILE")

# Un proceso escritor; instancias que apuntan al mismo bucket comparten exclusión.
_LOCKS: dict[Path, object] = {}
_LOCKS_GUARD = threading.Lock()


def _serializado(metodo):
    @wraps(metodo)
    def ejecutar(self, *args, **kwargs):
        with self._lock:
            return metodo(self, *args, **kwargs)

    return ejecutar


def _escribir_atomico(ruta: Path, contenido: bytes) -> None:
    temporal = None
    try:
        with NamedTemporaryFile(dir=ruta.parent, delete=False) as archivo:
            temporal = Path(archivo.name)
            archivo.write(contenido)
        os.replace(temporal, ruta)
    finally:
        if temporal is not None:
            temporal.unlink(missing_ok=True)


def _validar_object_name(object_name: str) -> None:
    """Rechaza claves que no respetan el formato del proyecto (§8.1, §11.3).

    Por qué importa tanto: el mock convierte object_name en RUTA de
    archivo. Si aceptáramos "../secrets.env", alguien podría escapar del
    directorio base y leer/escribir fuera (path traversal). OCI real no
    tiene ese riesgo, pero compartimos la validación para que un nombre
    válido en mock lo sea también en producción.
    """
    if not object_name or object_name.startswith("/") or "\\" in object_name:
        raise StorageInvalidName(f"object_name inválido: {object_name!r}")
    partes = object_name.split("/")
    if partes[0].casefold() == "_meta.json":
        raise StorageInvalidName("Nombre reservado para metadatos internos")
    if any(parte in ("", ".", "..") for parte in partes):
        raise StorageInvalidName(f"object_name inválido (segmento vacío o relativo): {object_name!r}")
    for parte in partes:
        if parte.endswith(".") or PureWindowsPath(parte).is_reserved():
            raise StorageInvalidName("Nombre no portable entre Windows y Linux")
        if not all(caracter.isalnum() or caracter in "._-@" for caracter in parte):
            raise StorageInvalidName(f"object_name con caracteres no permitidos: {object_name!r}")


class LocalMockStorageProvider(StorageProvider):
    """Proveedor de desarrollo: archivos locales con semántica idéntica a OCI.

    Constructor: `base_dir` permite apuntar el mock a un directorio
    específico (los tests usan un directorio temporal); por defecto usa
    `{DATA_DIR:-.data}/oci_mock_storage/`, la ubicación que §8.2 fija y que
    el .gitignore mantiene fuera de Git.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is None:
            # os.getenv lee variables de entorno; el segundo argumento es el
            # valor por defecto si no está definida (DATA_DIR del Apéndice A).
            data_dir = os.getenv("DATA_DIR", ".data")
            base_dir = Path(data_dir) / "oci_mock_storage"
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(self.base_dir, threading.RLock())

    # ------------------------- helpers internos -------------------------

    def _ruta(self, object_name: str) -> Path:
        """Traduce object_name a ruta de archivo DENTRO del directorio base."""
        _validar_object_name(object_name)
        ruta = (self.base_dir / object_name).resolve()
        if not ruta.is_relative_to(self.base_dir):
            raise StorageInvalidName("La ruta escapa del directorio de almacenamiento")
        return ruta

    def _leer_meta(self) -> dict:
        """Carga el índice de metadatos (vacío si nunca se escribió nada)."""
        archivo = self.base_dir / "_meta.json"
        if not archivo.exists():
            return {}
        return json.loads(archivo.read_text(encoding="utf-8"))

    def _escribir_meta(self, meta: dict) -> None:
        _escribir_atomico(self.base_dir / "_meta.json", json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

    @staticmethod
    def _etag_de(contenido: bytes) -> str:
        """Huella del contenido: SHA-256 truncado a 32 caracteres."""
        return hashlib.sha256(contenido).hexdigest()[:32]

    # ------------------------- operaciones -------------------------

    @_serializado
    def upload(
        self,
        object_name: str,
        content: bytes | str,
        *,
        content_type: str = "application/octet-stream",
        crear_solo: bool = False,
        if_match: str | None = None,
    ) -> StoredObject:
        # str se codifica a bytes una sola vez: de aquí en adelante todo es
        # binario, igual que en OCI (que no distingue texto de binario).
        contenido = content.encode("utf-8") if isinstance(content, str) else content

        if crear_solo and if_match is not None:
            raise StorageInvalidName("crear_solo e if_match son mutuamente excluyentes")

        ruta = self._ruta(object_name)
        existe = ruta.exists()

        if crear_solo and existe:
            raise StorageConflict(f"el objeto ya existe (crear_solo): {object_name}")
        if if_match is not None:
            if not existe:
                raise StorageNotFound(f"no existe el objeto a actualizar: {object_name}")
            etag_actual = self._etag_de(ruta.read_bytes())
            if etag_actual != if_match:
                # Alguien escribió entre nuestra lectura y esta escritura:
                # perder su cambio silenciosamente es lo que §7.2 prohíbe.
                raise StorageConflict(f"if_match={if_match} != etag actual {etag_actual}: {object_name}")

        ruta.parent.mkdir(parents=True, exist_ok=True)
        _escribir_atomico(ruta, contenido)

        etag = self._etag_de(contenido)
        meta = self._leer_meta()
        meta[object_name] = {
            "etag": etag,
            "size": len(contenido),
            "content_type": content_type,
            "last_modified": datetime.now(timezone.utc).isoformat(),
        }
        self._escribir_meta(meta)

        return StoredObject(
            object_name=object_name,
            etag=etag,
            # El prefijo mock:// es el "rótulo" que la UI detecta para
            # avisar «Almacenamiento local de desarrollo» (§8.2).
            uri=f"mock://{self.base_dir.name}/{object_name}",
            proveedor="mock",
        )

    @_serializado
    def get(self, object_name: str, *, if_match: str | None = None) -> bytes:
        ruta = self._ruta(object_name)
        if not ruta.exists():
            raise StorageNotFound(f"no existe el objeto: {object_name}")
        contenido = ruta.read_bytes()
        if if_match is not None and self._etag_de(contenido) != if_match:
            raise StorageConflict(f"if_match={if_match} != etag actual: {object_name}")
        return contenido

    def get_as_text(self, object_name: str, *, if_match: str | None = None) -> str:
        return self.get(object_name, if_match=if_match).decode("utf-8")

    @_serializado
    def list(
        self,
        prefix: str = "",
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> StoragePage:
        if limit < 1:
            raise ValueError(f"limit debe ser >= 1 (recibido: {limit})")

        nombres = sorted(
            nombre for nombre in self._leer_meta() if nombre.startswith(prefix) and (cursor is None or nombre > cursor)
        )

        pagina = nombres[:limit]
        resto = nombres[limit:]
        meta = self._leer_meta()

        items = [
            ListedObject(
                object_name=nombre,
                etag=meta[nombre]["etag"],
                size=meta[nombre]["size"],
                last_modified=datetime.fromisoformat(meta[nombre]["last_modified"]),
            )
            for nombre in pagina
        ]
        # El cursor es la última clave de la página; None cuando ya se
        # entregó todo.
        return StoragePage(items=items, next_cursor=pagina[-1] if resto else None)

    @_serializado
    def delete(self, object_name: str) -> None:
        ruta = self._ruta(object_name)
        if not ruta.exists():
            raise StorageNotFound(f"no existe el objeto a borrar: {object_name}")
        ruta.unlink()
        meta = self._leer_meta()
        meta.pop(object_name, None)
        self._escribir_meta(meta)


def get_storage_provider(base_dir: Path | str | None = None, *, configuracion=None) -> StorageProvider:
    """Fábrica del proveedor de almacenamiento según MOCK_OCI (§8.2).

    Recibe Configuracion validada; sin inyección la construye al llamar,
    leyendo entorno y .env con las mismas reglas que el backend.

    - MOCK_OCI=1 -> LocalMockStorageProvider. Sin red, sin credenciales.
    - MOCK_OCI=0 (o sin definir) -> proveedor real. HOY ese proveedor llega
      con el issue #14, así que falta-credenciales o no, el arranque falla
      con StorageConfigError. El mensaje lista QUÉ falta para que arreglar
      sea obvio — y nunca, en ninguna rama, se devuelve el mock en su
      lugar (criterio de aceptación 3 del issue #04).
    """
    try:
        from app.config import Configuracion

        ajustes = configuracion or Configuracion()
        ajustes.validar_critico()
    except (ValueError, RuntimeError) as error:
        raise StorageConfigError(
            "Configuración inválida: production no admite mocks; revisar APP_ENV y credenciales OCI"
        ) from error
    if ajustes.mock_oci:
        return LocalMockStorageProvider(
            base_dir=base_dir if base_dir is not None else Path(ajustes.data_dir) / "oci_mock_storage"
        )
    raise StorageConfigError(
        "MOCK_OCI=0 exige almacenamiento OCI real: el proveedor real se implementa en el issue #14. "
        "Configurar OCI_BUCKET_NAME, OCI_COMPARTMENT_ID, OCI_REGION, OCI_CONFIG_FILE y ese proveedor; "
        "para desarrollo/CI usar MOCK_OCI=1."
    )
