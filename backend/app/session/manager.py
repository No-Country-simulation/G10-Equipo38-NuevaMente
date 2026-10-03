import json
import secrets
import uuid

from app.jobs.store import RegistroOperativo, _hash_de
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.responses import SessionResponse, WorkspaceCreatedResponse
from app.storage.provider import StorageProvider


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

        try:
            with self.db.transaccion():
                # Se crea el workspace dentro de la transacción
                self.db.crear_workspace(workspace_id, codigo_recuperacion, self.workspace_retention_days)

                # Generación del token de sesión para el workspace recién creado
                token_sesion = self.__generar_token_sesion(workspace_id)

                # Persistencia en la Nube (OCI Object Storage)
                self.__subir_manifest_oci(workspace_id, codigo_recuperacion)

                return WorkspaceCreatedResponse(
                    workspace_id=workspace_id, recovery_code=codigo_recuperacion, token=token_sesion
                )

        except Exception:
            raise ErrorAplicacion(
                code=ErrorCode.INTERNAL,
                message="Hubo un error al crear el workspace, inténtelo nuevamente.",
            )

    def recuperar_sesion(self, codigo_recuperacion: str) -> SessionResponse:
        workspace = self.db.resolver_workspace(codigo_recuperacion)
        if workspace is None:
            raise ErrorAplicacion(
                code=ErrorCode.SESSION_INVALID,
                message="Código de recuperación inválido o espacio vencido.",
            )

        workspace_id = workspace["workspace_id"]
        token_sesion = self.__generar_token_sesion(workspace_id)

        return SessionResponse(workspace_id=workspace_id, token=token_sesion)

    def obtener_sesion(self, token: str) -> dict:
        return self.db.obtener_sesion(token)

    def revocar_sesion(self, token: str) -> None:
        self.db.revocar_sesion(token)

    def rotar_credenciales(self, workspace_id: str) -> tuple[str, str]:
        # Obtener el workspace actual antes de rotar las credenciales
        workspace = self.db.obtener_workspace(workspace_id)
        if not workspace:
            raise ValueError("Espacio de trabajo no encontrado")

        nueva_version = workspace.get("version_manifiesto", 1) + 1

        # Generar  nuevo código de recuperación y token
        nuevo_codigo_recuperacion = self.__generar_codigo_recuperacion()
        nuevo_token = secrets.token_hex(32)

        try:
            with self.db.transaccion():
                # Transacción atómica: Escritura agrupada en SQLite
                self.db.registrar_rotacion(
                    workspace_id,
                    nuevo_codigo_recuperacion,
                    nuevo_token,
                    nueva_version,
                    self.workspace_retention_days,
                    self.session_max_hours,
                )

                # Actualizar el manifiesto en la Nube (OCI Object Storage)
                # No se hace uso del if_match, ya que primero se aplica en la base de datos y ahi ya se tiene en cuenta
                # la versión actual del workspace (UPDATE workspaces .... WHERE workspace_id = ? AND version_manifiesto = ? )
                self.__subir_manifest_oci(workspace_id, nuevo_codigo_recuperacion, nueva_version)
        except Exception as e:
            raise ErrorAplicacion(
                code=ErrorCode.INTERNAL,
                message=f"Error al rotar las credenciales: {str(e)}",
            )

        return nuevo_codigo_recuperacion, nuevo_token

    def borrar_workspace(self, workspace_id: str) -> None:
        self.db.borrar_workspace(workspace_id)

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
        version: int = 1,
        if_match: str | None = None,
    ) -> None:
        manifest = {"workspace_id": workspace_id, "codigo_hash": _hash_de(codigo_recuperacion), "version": version}

        ruta_oci = f"workspaces/{workspace_id}/manifest.json"

        self.storage_provider.upload(ruta_oci, json.dumps(manifest).encode("utf-8"), if_match=if_match)
