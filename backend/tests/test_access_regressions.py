"""Regresiones de concurrencia, origen confiable y localización."""

import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.api import security
from app.api.deps import get_session_manager
from app.config import Configuracion
from app.main import crear_app
from app.schemas.errors import ErrorAplicacion, ErrorCode
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit
ORIGIN_SECRET = "origen-autenticado-solamente-para-tests"


@pytest.fixture
def client(tmp_path):
    security._peticiones_globales.clear()
    security._peticiones_ip.clear()
    security._intentos_fallidos_ip.clear()
    app = crear_app(
        Configuracion(
            _env_file=None,
            app_env="test",
            mock_oci=True,
            mock_gemini=True,
            data_dir=str(tmp_path),
            trusted_origin_secret=ORIGIN_SECRET,
        )
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    assert not security._intentos_en_curso


def test_cinco_plazas_incluyen_recuperaciones_en_vuelo(client):
    barrera = threading.Barrier(5)

    class Manager:
        def recuperar_sesion(self, code):
            barrera.wait(timeout=10)
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "Código inválido")

    client.app.dependency_overrides[get_session_manager] = lambda: Manager()

    def intentar(_):
        response = client.post("/api/sessions/recover", json={"recovery_code": "incorrecto"})
        return response.status_code, response.json()["error"]["code"]

    with ThreadPoolExecutor(max_workers=10) as pool:
        resultados = Counter(pool.map(intentar, range(10)))
    assert resultados == {(401, "SESSION_INVALID"): 5, (429, "RECOVERY_LOCKED"): 5}


def test_fallo_tecnico_libera_plaza_sin_contar_como_codigo_invalido(client):
    class Manager:
        def recuperar_sesion(self, code):
            raise ErrorAplicacion(ErrorCode.STORAGE_UNAVAILABLE, "Storage no disponible")

    client.app.dependency_overrides[get_session_manager] = lambda: Manager()
    for _ in range(6):
        response = client.post("/api/sessions/recover", json={"recovery_code": "codigo"})
        assert response.status_code == 503
    assert not security._intentos_en_curso
    assert sum(map(len, security._intentos_fallidos_ip.values())) == 0


def test_bloqueo_retorna_retry_after(client):
    for _ in range(5):
        assert client.post("/api/sessions/recover", json={"recovery_code": "incorrecto"}).status_code == 401
    response = client.post("/api/sessions/recover", json={"recovery_code": "incorrecto"})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


def test_origen_autenticado_separa_usuarios_del_mismo_frontend(client):
    def headers(ip):
        return {"X-NuevaMente-Client-IP": ip, "X-NuevaMente-Origin-Key": ORIGIN_SECRET}

    for _ in range(5):
        response = client.post("/api/workspaces", headers=headers("192.0.2.1"))
        assert response.status_code == 201
    assert client.post("/api/workspaces", headers=headers("192.0.2.1")).status_code == 429
    assert client.post("/api/workspaces", headers=headers("192.0.2.2")).status_code == 201


@pytest.mark.parametrize(
    "headers",
    [
        {"X-NuevaMente-Client-IP": "192.0.2.1"},
        {"X-NuevaMente-Client-IP": "192.0.2.1", "X-NuevaMente-Origin-Key": "clave-ajena"},
        {"X-NuevaMente-Client-IP": "texto", "X-NuevaMente-Origin-Key": ORIGIN_SECRET},
    ],
)
def test_no_confia_en_cabeceras_de_origen_no_autenticadas(client, headers):
    assert client.post("/api/workspaces", headers=headers).status_code == 400


def test_x_forwarded_for_del_cliente_no_permite_eludir_limites(client):
    for i in range(5):
        assert client.post("/api/workspaces", headers={"X-Forwarded-For": f"192.0.2.{i}"}).status_code == 201
    assert client.post("/api/workspaces", headers={"X-Forwarded-For": "192.0.2.99"}).status_code == 429


@pytest.mark.parametrize(
    "language, expected",
    [
        ("en", "Some form field has an invalid value."),
        ("pt-BR", "Algum campo do formulário tem um valor inválido."),
        ("fr, en;q=0.9, pt;q=0.5", "Some form field has an invalid value."),
        ("en;q=0, es;q=0.5", "La peticion no respeta el contrato del endpoint."),
    ],
)
def test_accept_language_localiza_sin_cambiar_codigo(client, language, expected):
    response = client.post("/api/sessions/recover", json={}, headers={"Accept-Language": language})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["message"] == expected


def test_error_no_manejado_tambien_respeta_idioma(client):
    @client.app.get("/boom")
    def boom():
        raise RuntimeError("secreto-no-publicar")

    response = client.get("/boom", headers={"Accept-Language": "en"})
    assert response.status_code == 500
    assert response.json()["error"]["message"].startswith("Internal error.")
    assert "secreto-no-publicar" not in response.text


def test_codigo_valido_no_borra_fallos_del_mismo_minuto(client):
    from app.schemas.responses import SessionResponse

    class Manager:
        def recuperar_sesion(self, code):
            if code == "valido":
                return SessionResponse(workspace_id="espacio", token="nuevo-token-simulado")
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "Código inválido")

    client.app.dependency_overrides[get_session_manager] = lambda: Manager()
    for _ in range(3):
        assert client.post("/api/sessions/recover", json={"recovery_code": "incorrecto"}).status_code == 401
    assert client.post("/api/sessions/recover", json={"recovery_code": "valido"}).status_code == 201
    for _ in range(2):
        assert client.post("/api/sessions/recover", json={"recovery_code": "incorrecto"}).status_code == 401
    response = client.post("/api/sessions/recover", json={"recovery_code": "incorrecto"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RECOVERY_LOCKED"
