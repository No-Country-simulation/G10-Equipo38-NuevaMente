from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.session.manager import SessionManager

security = HTTPBearer()


def get_session_manager(request: Request) -> SessionManager:

    config = request.app.state.config

    return SessionManager(
        db=request.app.state.db,
        storage_provider=request.app.state.storage_provider,
        workspace_retention_days=config.workspace_retention_days,
        session_max_hours=config.session_max_hours,
    )


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
