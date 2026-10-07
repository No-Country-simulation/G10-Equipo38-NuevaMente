"""El contenedor no inicia Streamlit antes de una API sana (#26)."""

import json
from unittest.mock import Mock
from urllib.error import URLError

import entrypoint
import pytest

pytestmark = pytest.mark.unit


def respuesta(status=200, datos=None):
    r = Mock()
    r.status = status
    r.read.return_value = json.dumps(datos if datos is not None else {"status": "ok"}).encode()
    r.__enter__ = Mock(return_value=r)
    r.__exit__ = Mock(return_value=False)
    return r


def test_espera_red_y_respuesta_sana(monkeypatch):
    cliente = Mock()
    cliente.open.side_effect = [URLError("sin conexión"), respuesta(503), respuesta(datos=[]), respuesta()]
    monkeypatch.setattr(entrypoint, "build_opener", lambda *args: cliente)
    monkeypatch.setattr(entrypoint.time, "sleep", lambda _: None)
    entrypoint.esperar_api("http://backend:8000/", timeout=5)
    assert cliente.open.call_count == 4
    assert cliente.open.call_args.args == ("http://backend:8000/api/health",)
    assert 0 < cliente.open.call_args.kwargs["timeout"] <= 3


def test_timeout_no_inicia_streamlit(monkeypatch):
    cliente = Mock()
    cliente.open.side_effect = URLError("no disponible")
    monkeypatch.setattr(entrypoint, "build_opener", lambda *args: cliente)
    monkeypatch.setattr(entrypoint.time, "monotonic", Mock(side_effect=[0, 0.1, 0.2, 1.1]))
    monkeypatch.setattr(entrypoint.time, "sleep", lambda _: None)
    monkeypatch.setenv("API_HEALTH_WAIT_SECONDS", "1")
    iniciar = Mock()
    monkeypatch.setattr(entrypoint.os, "execvp", iniciar)
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 1
    iniciar.assert_not_called()


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_rechaza_plazo_invalido(timeout):
    with pytest.raises(ValueError):
        entrypoint.esperar_api("http://backend:8000", timeout=timeout)


def test_reemplaza_proceso_tras_health(monkeypatch):
    esperar = Mock()
    iniciar = Mock()
    monkeypatch.setattr(entrypoint, "esperar_api", esperar)
    monkeypatch.setattr(entrypoint.os, "execvp", iniciar)
    monkeypatch.setenv("API_URL", "http://backend:8000")
    monkeypatch.setenv("API_HEALTH_WAIT_SECONDS", "12")
    entrypoint.main()
    esperar.assert_called_once_with("http://backend:8000", timeout=12)
    assert iniciar.call_args.args[0] == entrypoint.sys.executable
    assert iniciar.call_args.args[1][1:4] == ["-m", "streamlit", "run"]
