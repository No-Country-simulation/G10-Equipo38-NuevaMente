"""Regresiones de la revisión de issues 01–08 y 10."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from app.config import Configuracion, ConfiguracionIncompleta
from app.jobs.store import RegistroOperativo
from app.main import crear_app
from app.schemas.enums import DocumentStatus
from app.schemas.requests import GenerateRequest, ProgressEvent
from app.schemas.responses import EvaluacionCalidad, GenerationJobResponse, PedagogicalOutput
from app.storage.oci_storage import LocalMockStorageProvider, get_storage_provider
from app.storage.provider import StorageConfigError, StorageConflict, StorageInvalidName
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_schemas import FLASHCARDS, evaluacion_valida, salida_valida

pytestmark = pytest.mark.unit


def test_alcance_default_no_permite_fingir_cobertura():
    payload = dict(
        document_id="doc_1",
        perfil_destinatario="principiante",
        formato_salida="flashcards",
        nicho_sector="general",
        nivel_detalle="didactico",
        idioma_salida="pt",
    )
    assert GenerateRequest.model_validate(payload).alcance.tipo == "documento_completo"
    payload["alcance"] = {"tipo": "documento_completo", "secciones_cubiertas": ["todo"]}
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(payload)


def test_evento_flashcard_identifica_el_item():
    payload = dict(event_id="e1", tipo="flashcard_vista", generation_id="gen_1")
    with pytest.raises(ValidationError):
        ProgressEvent.model_validate(payload)
    assert ProgressEvent.model_validate({**payload, "flashcard_id": "fc_1"}).flashcard_id == "fc_1"


@pytest.mark.parametrize("reales", [False, True])
def test_suite_aisla_produccion_y_exige_mocks_desactivados_para_reales(monkeypatch, reales):
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    pytest_configure = runpy.run_path(str(Path(__file__).resolve().parents[2] / "conftest.py"))["pytest_configure"]

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MOCK_OCI", "1" if reales else "0")
    monkeypatch.setenv("MOCK_GEMINI", "1" if reales else "0")
    config = SimpleNamespace(
        option=SimpleNamespace(markexpr="integration_real" if reales else "unit or integration_mock")
    )
    if reales:
        with pytest.raises(pytest.UsageError):
            pytest_configure(config)
    else:
        import os

        pytest_configure(config)
        assert os.environ["APP_ENV"] == "test"
        assert os.environ["MOCK_OCI"] == os.environ["MOCK_GEMINI"] == "1"


def test_error_de_validador_responde_422_sin_input_ni_excepciones():
    app = crear_app(Configuracion(app_env="test"))

    @app.post("/validar")
    def validar(body: ProgressEvent):
        return body

    with TestClient(app, raise_server_exceptions=False) as client:
        respuesta = client.post("/validar", json={"event_id": "secreto", "tipo": "concepto_revisado"})
    assert respuesta.status_code == 422
    assert "secreto" not in respuesta.text
    assert set(respuesta.json()["error"]["details"]["errores"][0]) == {"loc", "msg", "type"}


def test_http_conserva_retry_after_y_sanitiza_request_id():
    app = crear_app(Configuracion(app_env="test"))

    @app.get("/limitado")
    def limitado():
        raise HTTPException(429, "Esperar", headers={"Retry-After": "60"})

    with TestClient(app) as client:
        respuesta = client.get("/limitado", headers={"X-Request-ID": "x" * 200})
    assert respuesta.headers["Retry-After"] == "60"
    assert respuesta.headers["X-Request-ID"].startswith("req_")


def test_cors_permite_idempotencia_y_reconexion():
    app = crear_app(Configuracion(app_env="test", cors_origins="https://frontend.test"))
    with TestClient(app) as client:
        respuesta = client.options(
            "/api/health",
            headers={
                "Origin": "https://frontend.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "idempotency-key,last-event-id",
            },
        )
    assert respuesta.status_code == 200


def test_produccion_rechaza_valores_vacios(tmp_path):
    credenciales = tmp_path / "config"
    credenciales.write_text("placeholder", encoding="utf-8")
    config = Configuracion(
        app_env="production",
        mock_oci=False,
        mock_gemini=False,
        google_api_key=" ",
        oci_compartment_id="",
        oci_region=" ",
        oci_bucket_name="",
        oci_config_file=str(credenciales),
    )
    with pytest.raises(ConfiguracionIncompleta) as error:
        config.validar_critico()
    for variable in ("GOOGLE_API_KEY", "OCI_COMPARTMENT_ID", "OCI_REGION", "OCI_BUCKET_NAME"):
        assert variable in str(error.value)


@pytest.mark.parametrize(
    "cambio",
    [
        {"anclaje_fuente_score": 0.98},
        {"razones_bloqueo": ["Cita inválida"]},
        {"verificacion_visual": "insuficiente"},
        {"cobertura_objetivos": "parcial"},
        {"cantidad_afirmaciones": 20, "cantidad_respaldadas": 19, "anclaje_fuente_score": 0.95},
    ],
)
def test_evaluacion_no_aprueba_comprobaciones_fallidas(cambio):
    with pytest.raises(ValidationError):
        EvaluacionCalidad.model_validate(evaluacion_valida(**cambio))


def test_diagnostico_sin_afirmaciones_no_inventa_score():
    diagnostico = EvaluacionCalidad.model_validate(
        evaluacion_valida(
            cantidad_afirmaciones=0, cantidad_respaldadas=0, anclaje_fuente_score=None, estado_evaluacion="no_evaluable"
        )
    )
    assert diagnostico.anclaje_fuente_score is None


def test_paquete_rechaza_evaluacion_no_aprobada_y_formato_incorrecto():
    payload = salida_valida(FLASHCARDS).model_dump(mode="json")
    payload["evaluacion_calidad"]["estado_evaluacion"] = "requiere_revision"
    with pytest.raises(ValidationError):
        PedagogicalOutput.model_validate(payload)
    payload["evaluacion_calidad"]["estado_evaluacion"] = "aprobada"
    payload["metadatos"]["formato_generado"] = "quiz"
    with pytest.raises(ValidationError):
        PedagogicalOutput.model_validate(payload)


def test_trabajo_completed_exige_persistencia_y_no_filtra_rechazados():
    payload = dict(generation_id="gen_01HX", status="completed", status_url="/status", events_url="/events")
    with pytest.raises(ValidationError):
        GenerationJobResponse.model_validate(payload)
    payload.update(
        contenido=salida_valida(FLASHCARDS), persistencia={"status_upload": "completado", "provider": "mock"}
    )
    GenerationJobResponse.model_validate(payload)
    payload["status"] = "rejected_quality"
    with pytest.raises(ValidationError):
        GenerationJobResponse.model_validate(payload)


@pytest.mark.parametrize("clave", ["_meta.json", "_META.JSON", "_meta.json.", "a/CON.txt", "a/fin."])
def test_mock_protege_indice_y_nombres_portables(tmp_path, clave):
    storage = LocalMockStorageProvider(tmp_path)
    storage.upload("valido.json", "contenido")
    with pytest.raises(StorageInvalidName):
        storage.upload(clave, "sobrescritura")
    assert len(storage.list().items) == 1


def test_mock_serializa_instancias_y_condiciones(tmp_path):
    stores = [LocalMockStorageProvider(tmp_path) for _ in range(8)]

    def escribir(i):
        try:
            stores[i % 8].upload("unico.json", str(i), crear_solo=True)
            ganador = True
        except StorageConflict:
            ganador = False
        stores[i % 8].upload(f"objetos/{i}.json", str(i))
        return ganador

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(escribir, range(32))) == 1
    assert len(stores[0].list().items) == 33


def test_factory_no_permite_mock_en_produccion(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MOCK_OCI", "1")
    with pytest.raises(StorageConfigError):
        get_storage_provider(tmp_path)


@pytest.fixture
def registro(tmp_path):
    store = RegistroOperativo(tmp_path / "review.sqlite")
    store.crear_workspace("ws_1", "codigo", 30)
    store.registrar_documento("doc_1", "ws_1", "Documento", DocumentStatus.READY)
    yield store
    store.cerrar()


def test_generacion_no_puede_usar_documento_de_otro_espacio(registro):
    registro.crear_workspace("ws_2", "codigo2", 30)
    with pytest.raises(ValueError):
        registro.registrar_generacion("gen_1", "ws_2", "doc_1")


def test_documento_borrado_oculta_consulta_directa_de_generacion(registro):
    registro.registrar_generacion("gen_1", "ws_1", "doc_1")
    registro.agendar_borrado("document", "doc_1", "ws_1")
    assert registro.obtener_generacion("gen_1") is None


def test_borrado_de_espacio_es_transaccion(registro, monkeypatch):
    registro.crear_sesion("token", "ws_1", 24)

    def fallo(*args):
        raise OSError("fallo al persistir lápida")

    monkeypatch.setattr(registro, "agendar_borrado", fallo)
    with pytest.raises(OSError):
        registro.borrar_workspace("ws_1")
    assert registro.obtener_workspace("ws_1") is not None
    assert registro.obtener_sesion("token") is not None


def test_sesion_no_sobrevive_a_espacio_expirado(registro):
    registro.crear_workspace("vencido", "codigo", -1)
    registro.crear_sesion("token", "vencido", 24)
    assert registro.obtener_sesion("token") is None


def test_idempotencia_no_reserva_sin_id_ni_permite_cambiarlo(registro):
    with pytest.raises(ValueError):
        registro.idempotencia_iniciar("ws_1", "generate", "clave", "{}")
    registro.idempotencia_iniciar("ws_1", "generate", "clave", "{}", "gen_1")
    with pytest.raises(ValueError):
        registro.idempotencia_completar("ws_1", "generate", "clave", "gen_2")
