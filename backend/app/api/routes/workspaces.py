from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import get_sesion_actual, get_session_manager, get_workspace_actual
from app.api.security import (
    RateLimiter,
    limpiar_intentos_fallidos,
    registrar_intento_fallido,
    validar_intentos_fallidos,
)
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.requests import RecoverSessionRequest
from app.schemas.responses import RotatedCodeResponse, SessionResponse, WorkspaceCreatedResponse
from app.session.manager import SessionManager

router = APIRouter(tags=["Workspaces"])
limitar_creacion = RateLimiter(key="workspace_creation", limite_global=50, limite_ip=5)
limitar_recuperacion = RateLimiter(key="workspace_recovery", limite_global=100, limite_ip=10)


@router.post(
    "/api/workspaces",
    response_model=WorkspaceCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear espacio anónimo",
    dependencies=[Depends(limitar_creacion)],
)
def create_workspace(manager: SessionManager = Depends(get_session_manager)) -> WorkspaceCreatedResponse:
    """
    Crea un espacio anónimo.
    Devuelve una sola vez el workspace_id, el código de recuperación
    (≥128 bits, agrupado) y el token de sesión.
    """
    return manager.crear_espacio()


@router.post(
    "/api/sessions/recover",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Recuperar espacio anónimo",
    dependencies=[Depends(limitar_recuperacion)],
)
def recover_workspace(
    request: Request, recovery_request: RecoverSessionRequest, manager: SessionManager = Depends(get_session_manager)
) -> SessionResponse:
    """
    Recupera un espacio anónimo existente.
    Devuelve una sola vez el workspace_id, el código de recuperación
    (≥128 bits, agrupado) y el token de sesión.
    """
    ip_cliente = validar_intentos_fallidos(request)

    try:
        # Aca se valida si el código de recuperación es correcto
        respuesta = manager.recuperar_sesion(recovery_request.recovery_code)
        limpiar_intentos_fallidos(ip_cliente)
        return respuesta
    except ErrorAplicacion as e:
        # Se registra el fallo si el código fue rechazado
        if e.error.code == ErrorCode.SESSION_INVALID:
            registrar_intento_fallido(ip_cliente)
        raise e


@router.delete(
    "/api/sessions/current", status_code=status.HTTP_204_NO_CONTENT, summary="Cerrar y revocar la sesión actual"
)
def close_session(
    sesion: str = Depends(get_sesion_actual), manager: SessionManager = Depends(get_session_manager)
) -> Response:
    """
    Revoca únicamente la sesión asociada al token enviado en la cabecera.
    """
    manager.revocar_sesion(sesion["token"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/workspaces/current/recovery-code",
    response_model=RotatedCodeResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotar código y revocar sesiones previas",
)
def rotate_recovery_code(
    workspace_id: str = Depends(get_workspace_actual), manager: SessionManager = Depends(get_session_manager)
) -> RotatedCodeResponse:
    """
    Invalida el código de recuperación anterior, revoca todas las sesiones
    asociadas al espacio y emite un nuevo código de recuperación y token.
    """
    nuevo_codigo, nuevo_token = manager.rotar_credenciales(workspace_id)

    return RotatedCodeResponse(recovery_code=nuevo_codigo, token=nuevo_token)


@router.delete(
    "/api/workspaces/current", status_code=status.HTTP_204_NO_CONTENT, summary="Borrar espacio y agendar limpieza"
)
def delete_workspace(
    workspace_id: str = Depends(get_workspace_actual), manager: SessionManager = Depends(get_session_manager)
) -> Response:
    """
    Borra el espacio de trabajo actual y agenda su limpieza.
    """
    manager.borrar_workspace(workspace_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
