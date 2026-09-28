from pathlib import Path

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import config
from app.jobs.store import RegistroOperativo
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.session.manager import SessionManager
from app.storage.oci_storage import get_storage_provider

security = HTTPBearer()

storage_provider = get_storage_provider()
archivo_db = Path(config.data_dir) / "operativo.db"
db_operativa = RegistroOperativo(ruta_db=archivo_db)


def get_session_manager() -> SessionManager:
    return SessionManager(db=db_operativa, storage_provider=storage_provider)


def get_sesion_actual(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    manager: SessionManager = Depends(get_session_manager),
) -> dict:
    token = credentials.credentials
    session = manager.obtener_sesion(token)

    if session is None:
        raise ErrorAplicacion(
            code=ErrorCode.SESSION_INVALID, message="Token inválido, expirado, revocado o espacio borrado."
        )

    session["token"] = token
    return session


def get_workspace_actual(sesion: dict = Depends(get_sesion_actual)) -> str:
    return sesion["workspace_id"]
