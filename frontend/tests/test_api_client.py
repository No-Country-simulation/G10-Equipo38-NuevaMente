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
            headers={"Content-Type": "application/json", "Accept-Language": "es"},
            timeout=(5.0, 30.0),
            allow_redirects=False,
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
            headers={"Content-Type": "application/json", "Accept-Language": "es"},
            timeout=(5.0, 30.0),
            allow_redirects=False,
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
                "Accept-Language": "es",
                "Authorization": "Bearer token_activo_123",
            },
            timeout=(5.0, 30.0),
            allow_redirects=False,
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


@pytest.mark.parametrize("payload", [None, [], {}, {"workspace_id": "ws", "token": 17, "recovery_code": "code"}])
def test_no_acepta_sesion_incompleta(client, payload):
    response = MagicMock(ok=True, status_code=201, headers={})
    response.json.return_value = payload
    with patch("requests.request", return_value=response):
        with pytest.raises(APIError, match="INVALID_RESPONSE"):
            client.create_workspace()


def test_json_invalido_es_fallo_tecnico(client):
    response = MagicMock(ok=True, status_code=200, headers={"X-Request-ID": "req_json"})
    response.json.side_effect = ValueError("contenido remoto privado")
    with patch("requests.request", return_value=response):
        with pytest.raises(APIError) as error:
            client.recover_session("codigo")
    assert error.value.code == "INVALID_RESPONSE"
    assert error.value.request_id == "req_json"
    assert "privado" not in str(error.value)


@pytest.mark.parametrize("status,payload,expected", [(401, [], "SESSION_INVALID"), (503, {"error": []}, "INTERNAL")])
def test_error_malformado_no_rompe_cliente(client, status, payload, expected):
    response = MagicMock(ok=False, status_code=status, headers={})
    response.json.return_value = payload
    with patch("requests.request", return_value=response):
        with pytest.raises(APIError) as error:
            client.get_documents("token")
    assert error.value.code == expected


def test_no_filtra_secretos_ni_reintenta_error_de_red(client, caplog):
    secreto = "token-secreto-y-codigo-de-recuperacion"
    with patch("requests.request", side_effect=requests.ConnectionError(secreto)) as request:
        with pytest.raises(APIError) as error:
            client.recover_session(secreto)
    assert request.call_count == 1
    assert secreto not in caplog.text
    assert secreto not in str(error.value)
    assert error.value.__suppress_context__


def test_no_loguea_mensaje_remoto_y_conserva_retry_after(client, caplog):
    response = MagicMock(ok=False, status_code=429, headers={"X-Request-ID": "req_limite", "Retry-After": "60"})
    response.json.return_value = {"error": {"code": "RECOVERY_LOCKED", "message": "codigo-secreto"}}
    with patch("requests.request", return_value=response):
        with pytest.raises(APIError) as error:
            client.recover_session("codigo-secreto")
    assert error.value.retry_after == "60"
    assert "req_limite" in caplog.text
    assert "codigo-secreto" not in caplog.text + str(error.value)


def test_redirect_no_reenvia_credenciales(client):
    response = MagicMock(ok=True, status_code=307, headers={"Location": "https://otro-destino.test"})
    with patch("requests.request", return_value=response) as request:
        with pytest.raises(APIError, match="INVALID_RESPONSE"):
            client.recover_session("codigo")
    assert request.call_count == 1
    assert request.call_args.kwargs["allow_redirects"] is False


def test_upload_multipart_y_clave_idempotente(client):
    response = MagicMock(ok=True, status_code=202, headers={})
    response.json.return_value = {"document_id": "doc", "status": "processing"}
    with patch("requests.request", return_value=response) as request:
        client.upload_file("token", "redes.pdf", b"%PDF", "application/pdf", title="Redes", idempotency_key="subida-1")
    args = request.call_args.kwargs
    assert args["files"] == {"file": ("redes.pdf", b"%PDF", "application/pdf")}
    assert args["data"] == {"documento_titulo": "Redes"}
    assert args["headers"] == {"Authorization": "Bearer token", "Idempotency-Key": "subida-1", "Accept-Language": "es"}
    assert args["json"] is None


def test_upload_texto_y_generacion_preservan_idempotencia(client):
    response = MagicMock(ok=True, status_code=202, headers={})
    response.json.return_value = {"status": "queued"}
    texto = {"documento_titulo": "Texto", "documento_contenido": "Una VCN es una red."}
    with patch("requests.request", return_value=response) as request:
        client.upload_document("token", texto, idempotency_key="texto-1")
        assert request.call_args.kwargs["json"] == texto
        assert request.call_args.kwargs["headers"]["Idempotency-Key"] == "texto-1"
        client.create_generation("token", {"document_id": "doc"}, idempotency_key="generacion-1")
        assert request.call_args.kwargs["headers"]["Idempotency-Key"] == "generacion-1"


@pytest.mark.parametrize("method", ["get_documents", "get_generations"])
def test_listados_conservan_paginacion_y_filtros(client, method):
    response = MagicMock(ok=True, status_code=200, headers={})
    response.json.return_value = {"items": [], "cursor": "siguiente"}
    params = {"limit": 10, "cursor": "anterior"}
    with patch("requests.request", return_value=response) as request:
        result = getattr(client, method)("token", params=params)
    assert result == response.json.return_value
    assert request.call_args.kwargs["params"] == params


def test_export_se_descarga_con_auth_sin_token_en_url(client):
    response = MagicMock(ok=True, status_code=200, headers={}, content=b"contenido exportado")
    with patch("requests.request", return_value=response) as request:
        assert client.download_export("token-privado", "gen_1", "pdf") == b"contenido exportado"
    args = request.call_args.kwargs
    assert args["url"] == "http://test-backend:8000/api/exports/gen_1"
    assert args["params"] == {"format": "pdf"}
    assert args["headers"]["Authorization"] == "Bearer token-privado"
    response.json.assert_not_called()


def test_api_url_se_lee_del_entorno(monkeypatch):
    monkeypatch.setenv("API_URL", "http://backend:8000/")
    assert APIClient().base_url == "http://backend:8000"


def test_headers_usan_idioma_y_solo_origen_de_ingreso():
    client = APIClient(
        idioma_ui="pt",
        origen_headers={
            "X-NuevaMente-Client-IP": "192.0.2.1",
            "X-NuevaMente-Origin-Key": "clave",
            "X-Forwarded-For": "falsificado",
            "Authorization": "ajeno",
        },
    )
    headers = client._headers("token")
    assert headers["Accept-Language"] == "pt"
    assert headers["X-NuevaMente-Client-IP"] == "192.0.2.1"
    assert headers["X-NuevaMente-Origin-Key"] == "clave"
    assert headers["Authorization"] == "Bearer token"
    assert "X-Forwarded-For" not in headers
