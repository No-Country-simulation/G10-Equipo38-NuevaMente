import logging
import secrets
import uuid

from app.jobs.store import RegistroOperativo, _hash_de
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.responses import SessionResponse, WorkspaceCreatedResponse
from app.session.manifests import sincronizar_manifiesto
from app.storage.provider import StorageConflict, StorageError, StorageProvider

logger = logging.getLogger("nuevamente.session")


class SessionManager:
    def __init__(
        self,
        db: RegistroOperativo,
        storage_provider: StorageProvider,
        workspace_retention_days: int,
        session_max_hours: int,
    ) -> None:
        self.db = db
        self.storage_provider = storage_provider
        self.workspace_retention_days = workspace_retention_days
        self.session_max_hours = session_max_hours

    def crear_espacio(self) -> WorkspaceCreatedResponse:
        workspace_id = str(uuid.uuid4())
        # Generación del código de recuperación
        codigo_recuperacion = self.__generar_codigo_recuperacion()

        with self.db.transaccion():
            # Se crea el workspace dentro de la transacción
            _, expira_en = self.db.crear_workspace(workspace_id, codigo_recuperacion, self.workspace_retention_days)

            # Generación del token de sesión para el workspace recién creado
            token_sesion = self.__generar_token_sesion(workspace_id)

            # Persistencia en la Nube (OCI Object Storage)
            self.__subir_manifest_oci(workspace_id, codigo_recuperacion, expira_en)

            return WorkspaceCreatedResponse(
                workspace_id=workspace_id, recovery_code=codigo_recuperacion, token=token_sesion
            )

    def recuperar_sesion(self, codigo_recuperacion: str) -> SessionResponse:
        workspace = self.db.resolver_workspace(codigo_recuperacion)
        if workspace is None:
            raise ErrorAplicacion(
                code=ErrorCode.SESSION_INVALID,
                message="Código de recuperación inválido o espacio vencido.",
            )

        workspace_id = workspace["workspace_id"]
        nueva_version_manifiesto = workspace.get("version_manifiesto", 1) + 1

        try:
            with self.db.transaccion():
                _, expira_en = self.db.renovar_expiracion_workspace(
                    workspace_id, self.workspace_retention_days, nueva_version_manifiesto
                )
                token_sesion = self.__generar_token_sesion(workspace_id)
                # Actualizar el manifiesto en la Nube (OCI Object Storage)
                self.__subir_manifest_oci(workspace_id, codigo_recuperacion, expira_en, nueva_version_manifiesto)
                return SessionResponse(workspace_id=workspace_id, token=token_sesion)
        except (StorageConflict, ValueError):
            raise ErrorAplicacion(
                code=ErrorCode.INVALID_STATE,
                message="El espacio cambió durante la recuperación; volver a intentarlo.",
            ) from None

    def obtener_sesion(self, token: str) -> dict | None:
        sesion = self.db.obtener_sesion(token)
        if sesion is not None:
            self.db.registrar_actividad(sesion["workspace_id"], self.workspace_retention_days)
        return sesion

    def revocar_sesion(self, token: str) -> None:
        self.db.revocar_sesion(token)

    def rotar_credenciales(self, workspace_id: str) -> tuple[str, str]:
        # Obtener el workspace actual antes de rotar las credenciales
        workspace = self.db.obtener_workspace(workspace_id)
        if not workspace:
            raise ErrorAplicacion(
                code=ErrorCode.SESSION_INVALID,
                message="Espacio de trabajo no encontrado o sesión expirada.",
            )

        nueva_version = workspace.get("version_manifiesto", 1) + 1

        # Generar  nuevo código de recuperación y token
        nuevo_codigo_recuperacion = self.__generar_codigo_recuperacion()
        nuevo_token = secrets.token_hex(32)

        try:
            with self.db.transaccion():
                # Transacción atómica: Escritura agrupada en SQLite
                _, expira_en = self.db.registrar_rotacion(
                    workspace_id,
                    nuevo_codigo_recuperacion,
                    nuevo_token,
                    nueva_version,
                    self.workspace_retention_days,
                    self.session_max_hours,
                )

                # Actualizar el manifiesto en la Nube (OCI Object Storage)
                self.__subir_manifest_oci(workspace_id, nuevo_codigo_recuperacion, expira_en, nueva_version)
        except (StorageConflict, ValueError):
            raise ErrorAplicacion(
                code=ErrorCode.INVALID_STATE,
                message="El espacio cambió durante la rotación; volver a intentarlo.",
            ) from None

        return nuevo_codigo_recuperacion, nuevo_token

    def borrar_workspace(self, workspace_id: str) -> None:
        self.db.borrar_workspace(workspace_id)
        try:
            sincronizar_manifiesto(self.db, self.storage_provider, workspace_id)
        except (StorageError, ValueError):
            # El acceso ya está bloqueado y la marca durable local queda para sincronizar.
            logger.warning("Borrado pendiente de confirmar en storage; workspace_id=%s", workspace_id)

    def __generar_token_sesion(self, workspace_id: str) -> str:
        # Token de sesión opaco (32 bytes = 256 bits)
        token_sesion = secrets.token_hex(32)
        self.db.crear_sesion(token_sesion, workspace_id, self.session_max_hours)
        return token_sesion

    def __generar_codigo_recuperacion(self) -> str:
        # Código de recuperación (16 bytes = 128 bits) agrupado
        raw_code = secrets.token_hex(16)
        codigo_recuperacion = "-".join(raw_code[i : i + 4] for i in range(0, 32, 4))
        return codigo_recuperacion

    def __subir_manifest_oci(
        self,
        workspace_id: str,
        codigo_recuperacion: str,
        expira_en: str,
        version: int = 1,
        if_match: str | None = None,
    ) -> None:
        workspace = self.db.workspace_para_manifest(workspace_id)
        if workspace is None or workspace["codigo_hash"] != _hash_de(codigo_recuperacion):
            raise ValueError("Manifiesto sin identidad operativa válida")
        sincronizar_manifiesto(self.db, self.storage_provider, workspace_id)
