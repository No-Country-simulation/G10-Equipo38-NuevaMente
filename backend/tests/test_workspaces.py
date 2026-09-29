import pytest
from app.api.security import _intentos_fallidos_ip, _peticiones_globales, _peticiones_ip
from app.config import Configuracion
from app.main import crear_app
from app.schemas.errors import ErrorCode
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def config_desarrollo(**sobrescrituras) -> Configuracion:
    """Config de desarrollo con mocks explícitos, inyectable y aislada."""
    valores = {"app_env": "development", "mock_oci": True, "mock_gemini": True}
    valores.update(sobrescrituras)
    return Configuracion(**valores)


@pytest.fixture
def cliente(tmp_path) -> TestClient:
    """App construida con config de desarrollo + cliente de pruebas con BD aislada."""
    # Inyectamos tmp_path en data_dir para aislar el SQLite (RegistroOperativo)
    config = config_desarrollo(data_dir=str(tmp_path))

    # raise_server_exceptions=False permite leer los JSON de error 500 si ocurren
    return TestClient(crear_app(config), raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def limpiar_rate_limits():
    """
    Vacia los diccionarios de seguridad en memoria antes de cada test.
    Garantiza que el bloqueo de un test no contamine al siguiente.
    """
    _peticiones_globales.clear()
    _peticiones_ip.clear()
    _intentos_fallidos_ip.clear()


def test_crear_y_recuperar_espacio(cliente):
    """Criterio 1: Crear -> cerrar sesión -> recuperar con código -> funciona."""
    # 1. Crear espacio
    resp_creacion = cliente.post("/api/workspaces")
    assert resp_creacion.status_code == 201
    datos_creacion = resp_creacion.json()

    workspace_id = datos_creacion["workspace_id"]
    codigo_recuperacion = datos_creacion["recovery_code"]
    token_inicial = datos_creacion["token"]

    # 2. Cerrar sesión
    resp_cierre = cliente.delete("/api/sessions/current", headers={"Authorization": f"Bearer {token_inicial}"})
    assert resp_cierre.status_code == 204

    # Validar que el token inicial ya no sirve
    resp_acceso_denegado = cliente.delete("/api/sessions/current", headers={"Authorization": f"Bearer {token_inicial}"})
    assert resp_acceso_denegado.status_code == 401

    # 3. Recuperar sesión con el código
    resp_recuperacion = cliente.post("/api/sessions/recover", json={"recovery_code": codigo_recuperacion})

    assert resp_recuperacion.status_code == 201
    datos_recuperacion = resp_recuperacion.json()

    assert datos_recuperacion["workspace_id"] == workspace_id
    # Verificamos si se emitio un token nuevo
    assert datos_recuperacion["token"] != token_inicial


def test_bloqueo_fuerza_bruta_recuperacion(cliente):
    """Criterio 2: Código inválido repetido -> 429 RECOVERY_LOCKED con bloqueo temporal."""
    # Intentar 5 veces con un código inventado
    for _ in range(5):
        resp = cliente.post("/api/sessions/recover", json={"recovery_code": "random-code-1234"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == ErrorCode.SESSION_INVALID.value

    # El sexto intento desde la misma IP (TestClient usa la misma IP simulada) debe ser bloqueado
    resp_bloqueada = cliente.post("/api/sessions/recover", json={"recovery_code": "random-code-1234"})
    assert resp_bloqueada.status_code == 429
    assert resp_bloqueada.json()["error"]["code"] == ErrorCode.RECOVERY_LOCKED.value


def test_rotar_codigo_invalida_sesiones(cliente):
    """Criterio 3: Rotar el código invalida el anterior y revoca sesiones previas."""
    # 1. Crear espacio
    resp_creacion = cliente.post("/api/workspaces")
    datos = resp_creacion.json()
    token_original = datos["token"]
    codigo_original = datos["recovery_code"]

    # 2. Ejecutar rotación
    resp_rotacion = cliente.post(
        "/api/workspaces/current/recovery-code", headers={"Authorization": f"Bearer {token_original}"}
    )
    assert resp_rotacion.status_code == 200
    datos_rotacion = resp_rotacion.json()

    nuevo_codigo = datos_rotacion["recovery_code"]
    nuevo_token = datos_rotacion["token"]

    # Validamos que haya generado las nuevas credenciales
    assert nuevo_codigo != codigo_original
    assert nuevo_token != token_original

    # 3. Validar que la sesión antigua fue revocada
    resp_vieja_sesion = cliente.delete("/api/sessions/current", headers={"Authorization": f"Bearer {token_original}"})
    assert resp_vieja_sesion.status_code == 401

    # 4. Validar que el código antiguo fue invalidado
    resp_viejo_codigo = cliente.post("/api/sessions/recover", json={"recovery_code": codigo_original})
    assert resp_viejo_codigo.status_code == 401


def test_borrar_espacio_revoca_acceso(cliente):
    """Criterio 4: Borrar espacio bloquea acceso de inmediato."""
    # 1. Crear espacio
    resp_creacion = cliente.post("/api/workspaces")
    token = resp_creacion.json()["token"]

    # 2. Borrar espacio
    resp_borrado = cliente.delete("/api/workspaces/current", headers={"Authorization": f"Bearer {token}"})
    assert resp_borrado.status_code == 204

    # 3. Validar que la sesión quedó revocada instantáneamente
    resp_acceso = cliente.post(
        "/api/workspaces/current/recovery-code",  # Cualquier endpoint protegido
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_acceso.status_code == 401
