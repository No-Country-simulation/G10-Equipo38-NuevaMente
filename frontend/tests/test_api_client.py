from unittest.mock import MagicMock, patch

import pytest
import requests
from api_client import APIClient, APIError

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    """Instancia del cliente HTTP configurada para pruebas."""
    return APIClient(base_url="http://test-backend:8000")


def test_create_workspace_exito(client):
    """Verifica que POST /api/workspaces retorne workspace_id, token y recovery_code."""
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.status_code = 201
    mock_response.headers = {"X-Request-ID": "req_123"}
    mock_response.json.return_value = {
        "workspace_id": "ws_123",
        "recovery_code": "code_abc",
        "token": "token_xyz",
    }

    with patch("requests.request", return_value=mock_response) as mock_req:
        res = client.create_workspace()

        assert res["workspace_id"] == "ws_123"
        assert res["recovery_code"] == "code_abc"
        assert res["token"] == "token_xyz"

        mock_req.assert_called_once_with(
            method="POST",
            url="http://test-backend:8000/api/workspaces",
            json=None,
            params=None,
            headers={"Content-Type": "application/json"},
            timeout=(5.0, 30.0),
        )


def test_recover_session_exito(client):
    """Verifica que POST /api/sessions/recover canjee el código por un nuevo token."""
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.status_code = 200
    mock_response.headers = {"X-Request-ID": "req_124"}
    mock_response.json.return_value = {
        "workspace_id": "ws_123",
        "token": "nuevo_token_456",
    }

    with patch("requests.request", return_value=mock_response) as mock_req:
        res = client.recover_session("code_abc")

        assert res["token"] == "nuevo_token_456"
        mock_req.assert_called_once_with(
            method="POST",
            url="http://test-backend:8000/api/sessions/recover",
            json={"recovery_code": "code_abc"},
            params=None,
            headers={"Content-Type": "application/json"},
            timeout=(5.0, 30.0),
        )


def test_close_session_header_authorization(client):
    """Verifica que DELETE /api/sessions/current envíe el Bearer token y procese 204 No Content."""
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.status_code = 204
    mock_response.headers = {"X-Request-ID": "req_125"}

    with patch("requests.request", return_value=mock_response) as mock_req:
        client.close_session("token_activo_123")

        mock_req.assert_called_once_with(
            method="DELETE",
            url="http://test-backend:8000/api/sessions/current",
            json=None,
            params=None,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer token_activo_123",
            },
            timeout=(5.0, 30.0),
        )


def test_rotate_recovery_code_exito(client):
    """Verifica POST /api/workspaces/current/recovery-code."""
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.status_code = 200
    mock_response.headers = {"X-Request-ID": "req_126"}
    mock_response.json.return_value = {
        "workspace_id": "ws_123",
        "recovery_code": "nuevo_codigo_789",
        "token": "token_rotado_999",
    }

    with patch("requests.request", return_value=mock_response):
        res = client.rotate_recovery_code("token_viejo")
        assert res["recovery_code"] == "nuevo_codigo_789"
        assert res["token"] == "token_rotado_999"


def test_delete_workspace_exito(client):
    """Verifica DELETE /api/workspaces/current."""
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.status_code = 204
    mock_response.headers = {"X-Request-ID": "req_127"}

    with patch("requests.request", return_value=mock_response):
        res = client.delete_workspace("token_activo_123")
        assert res is None


def test_desempaquetado_error_backend(client):
    """Verifica que respuestas 4xx/5xx desempaqueten el error.code y el request_id."""
    mock_response = MagicMock()
    mock_response.ok = False
    mock_response.status_code = 401
    mock_response.headers = {"X-Request-ID": "req_error_001"}
    mock_response.json.return_value = {
        "error": {
            "code": "SESSION_INVALID",
            "message": "La sesión venció o no existe.",
            "details": {},
        },
        "request_id": "req_error_001",
    }

    with patch("requests.request", return_value=mock_response):
        with pytest.raises(APIError) as exc_info:
            client.close_session("token_expirado")

        err = exc_info.value
        assert err.code == "SESSION_INVALID"
        assert err.status_code == 401
        assert err.request_id == "req_error_001"


def test_error_red_conexion_caida(client):
    """Verifica que fallos de conexión HTTP lanzen APIError con SERVICE_UNAVAILABLE (503)."""
    with patch(
        "requests.request",
        side_effect=requests.exceptions.ConnectionError("Connection refused"),
    ):
        with pytest.raises(APIError) as exc_info:
            client.create_workspace()

        err = exc_info.value
        assert err.code == "SERVICE_UNAVAILABLE"
        assert err.status_code == 503
