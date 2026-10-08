"""La herramienta real no acepta mocks; el flujo se prueba con SDK simulado."""

import json

import pytest
import test_storage_oci as casos
from app.storage.provider import StorageConfigError, StorageUnavailable
from app.tools.verify_oci import main, verificar

entorno = casos.entorno
pytestmark = pytest.mark.unit


def test_smoke_put_get_list_cleanup_con_doble_de_sdk(entorno, monkeypatch):
    cfg, sdk, crear, _ = entorno
    monkeypatch.setattr("app.tools.verify_oci.OCIObjectStorageProvider", lambda cfg, **kw: crear(**kw))
    resultado = verificar(cfg)
    assert resultado["originales_y_paquetes"] == 2 and resultado["temporales_eliminados"]
    assert resultado["solicitudes_esta_ejecucion"] == len(sdk.calls)
    assert sdk.objetos == {} and resultado["presupuesto"]["bytes_reservados"] == 0
    assert "prueba_tecnica" in json.loads([c[1][3] for c in sdk.calls if c[0] == "put"][1])


def test_smoke_rechaza_mock_y_no_emite_sdk(entorno):
    cfg, sdk, _, _ = entorno
    cfg.mock_oci = True
    with pytest.raises(StorageConfigError, match="mock"):
        verificar(cfg)
    assert sdk.calls == []


def test_smoke_fallido_no_deja_el_original_que_ya_escribio(entorno, monkeypatch):
    cfg, sdk, crear, _ = entorno
    proveedor = crear()
    original = proveedor.upload
    llamadas = []

    def fallar(nombre, *args, **kwargs):
        llamadas.append(nombre)
        if len(llamadas) == 2:
            raise StorageUnavailable("error")
        return original(nombre, *args, **kwargs)

    monkeypatch.setattr(proveedor, "upload", fallar)
    monkeypatch.setattr("app.tools.verify_oci.OCIObjectStorageProvider", lambda *args, **kw: proveedor)
    with pytest.raises(StorageUnavailable):
        verificar(cfg)
    assert sdk.objetos == {}


def test_cli_error_no_filtra_credenciales(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "app.tools.verify_oci.verificar", lambda *args, **kw: (_ for _ in ()).throw(ValueError("token-super-secreto"))
    )
    assert main(["--env-file", str(tmp_path / "ausente")]) == 1
    assert "token-super-secreto" not in capsys.readouterr().out


def test_reportes_anteriores_no_se_sobrescriben_ni_acreditan_ejecucion(tmp_path, monkeypatch, capsys):
    reporte = tmp_path / "anterior.json"
    reporte.write_text("informe anterior", encoding="utf-8")

    def no_ejecutar(*args, **kwargs):
        pytest.fail("No debe iniciar otra prueba usando el nombre de un snapshot anterior")

    monkeypatch.setattr("app.tools.verify_oci.verificar", no_ejecutar)
    assert main(["--report", str(reporte)]) == 2
    assert reporte.read_text(encoding="utf-8") == "informe anterior"
    assert "ya existe" in capsys.readouterr().out
