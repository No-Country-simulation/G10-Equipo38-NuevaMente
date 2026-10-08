"""Flujos de sesión: HTTP local real; OCI y Gemini simulados explícitamente."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests
from api_client import APIClient, APIError
from i18n import t
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.integration_mock
APP = Path(__file__).resolve().parents[1] / "app.py"
ROOT = APP.parent.parent


@pytest.fixture
def backend_http(tmp_path, monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update(
        APP_ENV="ci",
        MOCK_OCI="1",
        MOCK_GEMINI="1",
        GOOGLE_API_KEY="placeholder",
        DATA_DIR=str(tmp_path / "backend"),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(ROOT / "backend"),
    )
    command = (
        "import sys; from app.config import Configuracion; Configuracion.model_config['env_file']=None; "
        "import uvicorn; uvicorn.run('app.main:app',host='127.0.0.1',port=int(sys.argv[1]),"
        "log_level='error',access_log=False)"
    )
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", command, str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(f"El backend de prueba no pudo iniciar: {process.returncode}")
            try:
                if requests.get(url + "/api/health", timeout=0.2).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.05)
        else:
            pytest.fail("El backend de prueba no respondió al health local")
        monkeypatch.setenv("API_URL", url)
        yield APIClient(url)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _click(app, key, lang="es"):
    button = next(button for button in app.button if button.label == t(key, lang))
    button.click().run()
    assert not app.exception
    return app


def test_cliente_con_backend_http_recupera_rota_y_borra(backend_http):
    initial = backend_http.create_workspace()
    backend_http.close_session(initial["token"])
    with pytest.raises(APIError) as expired:
        backend_http.close_session(initial["token"])
    assert expired.value.status_code == 401
    recovered = backend_http.recover_session(initial["recovery_code"])
    assert recovered["workspace_id"] == initial["workspace_id"]
    assert recovered["token"] != initial["token"]
    rotated = backend_http.rotate_recovery_code(recovered["token"])
    with pytest.raises(APIError) as old_token:
        backend_http.close_session(recovered["token"])
    assert old_token.value.status_code == 401
    with pytest.raises(APIError) as old_code:
        backend_http.recover_session(initial["recovery_code"])
    assert old_code.value.status_code == 401
    backend_http.delete_workspace(rotated["token"])
    with pytest.raises(APIError) as deleted:
        backend_http.recover_session(rotated["recovery_code"])
    assert deleted.value.status_code == 401


def test_streamlit_recuperacion_confirmaciones_y_codigo_unico(backend_http):
    app = AppTest.from_file(str(APP)).run()
    assert not app.exception
    workspace = app.session_state.workspace_id
    recovery = app.session_state.recovery_code
    token = app.session_state.session_token
    assert app.get("code")[0].value == recovery
    assert all(token not in code.value for code in app.get("code"))
    _click(app, "onboarding.codigo_guardado")
    assert app.session_state.recovery_code is None
    app.run()
    assert not app.get("code")
    app.session_state["documento_temporal"] = "datos del espacio anterior"
    _click(app, "sidebar.cerrar_sesion")
    assert app.session_state.session_token is None
    assert "documento_temporal" not in app.session_state
    app.text_input[0].set_value(recovery)
    _click(app, "onboarding.recuperar_boton")
    assert app.session_state.workspace_id == workspace
    assert app.session_state.session_token != token
    assert app.success[0].value == t("onboarding.recuperado")
    _click(app, "onboarding.rotar_codigo")
    _click(app, "comun.no")
    assert app.session_state.recovery_code is None
    _click(app, "onboarding.rotar_codigo")
    _click(app, "comun.si")
    assert app.session_state.recovery_code != recovery
    rotated_token = app.session_state.session_token
    _click(app, "onboarding.borrar_espacio")
    _click(app, "comun.no")
    assert app.session_state.session_token == rotated_token
    _click(app, "onboarding.borrar_espacio")
    _click(app, "comun.si")
    assert app.session_state.workspace_id != workspace
    assert any(t("onboarding.borrado_recibido") == item.value for item in app.success)


@pytest.mark.parametrize("lang", ["es", "en", "pt"])
def test_error_inicial_desconocido_es_amigable(monkeypatch, lang):
    def fail(_):
        raise APIError("NUEVO_ERROR", "mensaje-remoto-privado", 503, "req_fallo")

    monkeypatch.setattr(APIClient, "create_workspace", fail)
    app = AppTest.from_file(str(APP))
    app.session_state.idioma_ui = lang
    app.run()
    assert not app.exception
    assert t("errors.INTERNAL", lang) in app.error[0].value
    assert "NUEVO_ERROR" not in app.error[0].value
    assert "NUEVO_ERROR" in app.get("code")[0].value
    assert all("mensaje-remoto-privado" not in item.value for item in app.get("code"))


@pytest.fixture
def workspace_ui(monkeypatch):
    monkeypatch.setattr(
        APIClient,
        "create_workspace",
        lambda _: {
            "workspace_id": "ws_prueba",
            "token": "token_solo_servidor",
            "recovery_code": "codigo_prueba",
        },
    )
    app = AppTest.from_file(str(APP)).run()
    assert not app.exception
    return app


@pytest.mark.parametrize("action", ["sidebar.cerrar_sesion", "onboarding.rotar_codigo", "onboarding.borrar_espacio"])
def test_fallo_tecnico_conserva_sesion_y_permita_reintentar(workspace_ui, monkeypatch, action):
    def fail(*_):
        raise APIError("STORAGE_UNAVAILABLE", "datos-remotos-privados", 503, "req_tecnico")

    for method in ("close_session", "rotate_recovery_code", "delete_workspace"):
        monkeypatch.setattr(APIClient, method, fail)
    app = _click(workspace_ui, action)
    if action != "sidebar.cerrar_sesion":
        _click(app, "comun.si")
        assert app.session_state.accion_confirmar in ("rotar", "borrar")
    assert app.session_state.session_token == "token_solo_servidor"
    assert t("errors.STORAGE_UNAVAILABLE") in app.error[0].value
    assert all("datos-remotos-privados" not in code.value for code in app.get("code"))


def test_sesion_401_pide_codigo_y_limpia_datos(workspace_ui, monkeypatch):
    def fail(*_):
        raise APIError("SESSION_INVALID", "sesión revocada", 401, "req_sesion")

    monkeypatch.setattr(APIClient, "close_session", fail)
    app = workspace_ui
    app.session_state["borrador_anterior"] = "privado"
    _click(app, "sidebar.cerrar_sesion")
    assert app.session_state.session_token is None
    assert "borrador_anterior" not in app.session_state
    assert app.session_state.mostrando_recuperacion
    assert app.text_input
    assert any(t("errors.SESSION_INVALID") in warning.value for warning in app.warning)


@pytest.mark.parametrize("lang", ["es", "en", "pt"])
def test_recuperacion_bloqueada_se_traduce(workspace_ui, monkeypatch, lang):
    def fail(*_):
        raise APIError("RECOVERY_LOCKED", "codigo_privado", 429, "req_lock", retry_after="60")

    monkeypatch.setattr(APIClient, "recover_session", fail)
    app = workspace_ui
    app.session_state.idioma_ui = lang
    app.run()
    _click(app, "onboarding.recuperar_titulo", lang)
    app.text_input[0].set_value("codigo_invalido")
    _click(app, "onboarding.recuperar_boton", lang)
    assert t("errors.RECOVERY_LOCKED", lang) in app.error[0].value
    assert any(t("errors.reintentar_en", lang).format(segundos="60") == item.value for item in app.caption)
    assert "RECOVERY_LOCKED" not in app.error[0].value
