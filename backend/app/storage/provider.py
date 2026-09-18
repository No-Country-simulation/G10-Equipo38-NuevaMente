"""Interfaz `StorageProvider` y errores propios (issue #04, referencia §8).

Por qué existe una "interfaz": el proyecto tiene DOS formas de guardar
contenido — Object Storage real de Oracle (OCI, obligatorio para la entrega)
y un mock local en `.data/` para desarrollar y correr la CI sin red ni
credenciales. En vez de regar `if MOCK_OCI:` por todo el código, definimos
aquí un CONTRATO único (`StorageProvider`) con las mismas operaciones y la
misma semántica; cada implementación lo cumple. El resto del backend
depende de la interfaz, nunca de una implementación concreta (esto se
llama "inversión de dependencias" y es lo que permite probar sin red).

Cómo se lee esto si no conocés los ABC de Python: `ABC` significa "Abstract
Base Class". Un método marcado con `@abstractmethod` NO tiene cuerpo
implementado: obliga a toda clase que herede a implementarlo. Es la forma
de Python de decir "todo proveedor de almacenamiento debe saber hacer
exactamente estas cinco cosas".

La semántica condicional (ETag) en lenguaje llano: un ETag es una "huella"
del contenido actual de un objeto. Sirve para dos cosas:

- CREAR sin pisar: `upload(..., crear_solo=True)` falla con
  `StorageConflict` si el objeto ya existe (nadie sobreescribe un manifiesto
  ajeno por accidente).
- ACTUALIZAR sin perder cambios: `upload(..., if_match=etag)` solo escribe
  si el objeto sigue teniendo la huella que el llamador leyó. Si otra
  pestaña lo cambió en el medio, la huella ya no coincide y falla con
  `StorageConflict`: quien quiera escribir debe releer y reintentar. Es el
  "control de versión" que §7.2 exige para manifiestos y progreso.

Mock y OCI real DEBEN comportarse idéntico ante estas operaciones (lo
verifican backend/tests/test_storage.py): si no, lo que pasa en desarrollo
no predice lo que pasa en producción.

Congelación de comportamiento: esta interfaz es el contrato del carril INF
(guía §2: StorageProvider + manifiestos). Cambiar firmas o semántica exige
avisar a los consumidores (issues #14, #19, #31...).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

# -----------------------------------------------------------------------------
# Errores propios del almacenamiento
# -----------------------------------------------------------------------------


class StorageError(Exception):
    """Error base de todo el subsistema de almacenamiento.

    Tener una jerarquía propia permite a la capa de API traducir cada fallo
    al HTTP correcto SIN conocer detalles del proveedor (mock u OCI).
    """


class StorageUnavailable(StorageError):
    """El almacenamiento no responde (red caída, servicio no disponible).

    La capa de API lo traduce a HTTP 503 con error.code=STORAGE_UNAVAILABLE
    (§3.3 y §7.3). Regla crítica: un trabajo cuya persistencia falla así
    queda `failed`, NUNCA `completed` ni `rejected_quality`.
    """


class StorageNotFound(StorageError):
    """El objeto pedido no existe (o fue borrado).

    La capa de API lo traduce a 404 NOT_FOUND.
    """


class StorageConflict(StorageError):
    """La operación condicional no se cumplió (§7.2: control de versión).

    Dos casos: crear un objeto que ya existe (`crear_solo=True`), o
    actualizar con un `if_match` cuya huella ya no es la actual (alguien
    escribió en el medio). La capa de API lo traduce a 409 INVALID_STATE /
    IDEMPOTENCY_CONFLICT según el contexto.
    """


class StorageInvalidName(StorageError):
    """El object_name no respeta el formato de claves del proyecto.

    §8.1: las claves usan identificadores generados (sin rutas absolutas,
    sin `..`, sin espacios). Rechazar aquí impide que un nombre subido por
    un usuario se convierta en una ruta del sistema de archivos (§11.3).
    """


class StorageConfigError(StorageError):
    """Configuración insuficiente para arrancar el proveedor REAL.

    §8.2: con MOCK_OCI=0 y credenciales faltantes el arranque falla con un
    mensaje accionable (qué variables faltan). NUNCA se convierte en mock
    automáticamente: eso convertiría un fallo de producción en un éxito
    falso (criterio de aceptación 3 del issue y criterio 10 de §12.2).
    """


# -----------------------------------------------------------------------------
# Valores que devuelven las operaciones
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredObject:
    """Respuesta de `upload`: dónde quedó el objeto y su nueva huella.

    - `etag`: huella del contenido recién escrito; guárdala si pensás
      actualizar el objeto después (pásala en `if_match`).
    - `uri`: identificador del proveedor. El mock devuelve `mock://...`
      para que la UI pueda rotular «Almacenamiento local de desarrollo»
      (§8.2); OCI real devolverá `oci://bucket/objeto` (issue #14).
    """

    object_name: str
    etag: str
    uri: str
    proveedor: str


@dataclass(frozen=True)
class ListedObject:
    """Un objeto devuelto por `list` (sin su contenido, solo metadata)."""

    object_name: str
    etag: str
    size: int
    last_modified: datetime | None


@dataclass(frozen=True)
class StoragePage:
    """Una página de resultados de `list`.

    `next_cursor` es la clave del ÚLTIMO objeto de la página: pasala de
    nuevo como `cursor` para pedir la siguiente (semántica "start-after").
    `None` significa que no hay más objetos. Así ningún listado puede
    devolver un resultado gigante de una sola vez (regla 4 de
    contratos-api.md: todo listado es paginado con limit/cursor).
    """

    items: list[ListedObject]
    next_cursor: str | None


# -----------------------------------------------------------------------------
# La interfaz propiamente dicha
# -----------------------------------------------------------------------------


class StorageProvider(ABC):
    """Contrato único de persistencia de contenidos (§8).

    Implementaciones:

    - `LocalMockStorageProvider` (storage/oci_storage.py, issue #04):
      archivos locales bajo `.data/oci_mock_storage/`, para desarrollo y CI
      con MOCK_OCI=1. Sin red, sin credenciales, sin costo.
    - `OCIObjectStorageProvider` (issue #14): Object Storage real de Oracle,
      bucket "nuevamente-contenidos-educativos", obligatorio para la
      entrega. Debe respetar ESTA misma semántica al milímetro.

    Convenciones de object_name (prefijos oficiales de §8.3):

    - workspaces/{workspace_id}/manifest.json
    - source_documents/{workspace_id}/{document_id}/original
    - source_documents/{workspace_id}/{document_id}/manifest.json
    - outputs/{workspace_id}/{generation_id}/content.json
    - exports/{workspace_id}/{generation_id}/{formato}
    - progress/{workspace_id}/state.json
    - demo/...
    """

    @abstractmethod
    def upload(
        self,
        object_name: str,
        content: bytes | str,
        *,
        content_type: str = "application/octet-stream",
        crear_solo: bool = False,
        if_match: str | None = None,
    ) -> StoredObject:
        """Escribe `content` en `object_name` y devuelve su huella nueva.

        Modos (mutuamente excluyentes; pasar ambos es error del llamador):

        - Sin condiciones: escritura incondicional (crea o pisa).
        - `crear_solo=True`: falla con StorageConflict si ya existe. Es el
          "if-none-match=*" de OCI: garantiza creación sin sobrescribir.
        - `if_match=etag`: falla con StorageConflict si la huella actual no
          es `etag`, y con StorageNotFound si el objeto no existe. Es el
          "if-match" de OCI: actualización sin perder cambios ajenos.
        """

    @abstractmethod
    def get(self, object_name: str, *, if_match: str | None = None) -> bytes:
        """Lee el contenido crudo del objeto.

        StorageNotFound si no existe. `if_match` permite una lectura
        consistente (falla con StorageConflict si cambió desde que el
        llamador leyó su huella): útil para decidir un update condicional.
        """

    @abstractmethod
    def get_as_text(self, object_name: str, *, if_match: str | None = None) -> str:
        """Igual que `get` pero decodificado como UTF-8.

        Atajo para los JSON del proyecto (manifiestos, paquetes, progreso).
        """

    @abstractmethod
    def list(
        self,
        prefix: str = "",
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> StoragePage:
        """Lista los objetos que arrancan con `prefix`, en orden lexicográfico.

        Paginado con `limit` (máximo permitido) y `cursor` (exclusivo:
        devuelve objetos estrictamente posteriores a esa clave). Nunca lanza
        por "demasiados resultados": el llamador sigue paginando.
        """

    @abstractmethod
    def delete(self, object_name: str) -> None:
        """Borra el objeto. StorageNotFound si no existe.

        El borrado físico puede diferirse según la política de retención
        (§8.5 agenda limpieza), pero desde esta interfaz el objeto deja de
        ser accesible inmediatamente: get/list posteriores no lo devuelven.
        """
