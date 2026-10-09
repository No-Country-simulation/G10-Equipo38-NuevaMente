"""Manifiestos privados, escrituras condicionales y sincronización de actividad/lápidas."""

import logging
import threading

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.jobs.store import RegistroOperativo
from app.storage.object_keys import clave_workspace
from app.storage.provider import StorageConflict, StorageError, StorageProvider

logger = logging.getLogger("nuevamente.session")


class Lapida(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recurso_tipo: str = Field(pattern=r"^(workspace|document|generation)$")
    recurso_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    solicitado_en: AwareDatetime
    purga_despues_en: AwareDatetime


class ManifiestoEspacio(BaseModel):
    """Admite los manifiestos anteriores; nunca incluye tokens ni códigos en claro."""

    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    codigo_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: int = Field(gt=0, strict=True)
    expira_en: AwareDatetime
    ultima_actividad_en: AwareDatetime | None = None
    borrado: bool = False
    borrado_en: AwareDatetime | None = None
    recursos_borrados: list[Lapida] = Field(default_factory=list)


def sincronizar_manifiesto(db: RegistroOperativo, storage: StorageProvider, workspace_id: str) -> None:
    datos = db.workspace_para_manifest(workspace_id)
    if datos is None:
        return
    manifest = ManifiestoEspacio(
        workspace_id=workspace_id,
        codigo_hash=datos["codigo_hash"],
        version=datos["version_manifiesto"],
        expira_en=datos["expira_en"],
        ultima_actividad_en=datos["ultima_actividad_en"],
        borrado=datos["borrado_en"] is not None,
        borrado_en=datos["borrado_en"],
        recursos_borrados=db.listar_borrados(workspace_id),
    )
    key = clave_workspace(workspace_id)
    contenido = manifest.model_dump_json().encode("utf-8")
    pagina = storage.list(key, limit=1)
    objeto = next((item for item in pagina.items if item.object_name == key), None)
    if objeto is None:
        storage.upload(key, contenido, crear_solo=True, content_type="application/json")
    else:
        previo = ManifiestoEspacio.model_validate_json(storage.get(key, if_match=objeto.etag))
        if previo.workspace_id != workspace_id or previo.version > manifest.version:
            raise StorageConflict("Versión de manifiesto no compatible")
        if (previo.borrado or previo.borrado_en) and not manifest.borrado:
            raise StorageConflict("Un manifiesto retirado no puede reactivarse")
        if previo.version == manifest.version:
            if previo != manifest:
                raise StorageConflict("Una versión identifica manifiestos diferentes")
        else:
            storage.upload(key, contenido, if_match=objeto.etag, content_type="application/json")
    db.confirmar_manifiesto(workspace_id, manifest.version)


def sincronizar_pendientes(db: RegistroOperativo, storage: StorageProvider) -> int:
    confirmados = 0
    for workspace_id in db.listar_manifiestos_pendientes():
        try:
            sincronizar_manifiesto(db, storage, workspace_id)
        except (StorageError, ValueError):
            # No registrar secretos ni mensajes crudos del proveedor. La marca persiste.
            logger.warning("Manifiesto pendiente de sincronización; workspace_id=%s", workspace_id)
        else:
            confirmados += 1
    return confirmados


class SincronizadorManifiestos:
    """Agrupa escrituras; cada operación conserva exclusivamente los retries de OCI."""

    def __init__(self, db: RegistroOperativo, storage: StorageProvider, intervalo: float = 60):
        self.db, self.storage, self.intervalo = db, storage, intervalo
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._ejecutar, name="manifest-sync", daemon=True)

    def iniciar(self) -> None:
        self._thread.start()

    def _ejecutar(self) -> None:
        while not self._stop.wait(self.intervalo):
            sincronizar_pendientes(self.db, self.storage)

    def detener(self) -> None:
        self._stop.set()
        # No cerrar SQLite mientras una escritura condicional esté en vuelo.
        self._thread.join()
