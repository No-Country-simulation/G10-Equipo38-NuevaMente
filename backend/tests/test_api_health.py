"""Tests del esqueleto FastAPI (issue #07).

Verificación exacta que pide el issue: `pytest backend/tests/test_api_health.py`
más arranque con config rota (simulado construyendo la app con configuraciones
incompletas: si crear_app() lanza, el proceso nunca abre puerto).

Qué se prueba, criterio por criterio:

1. /api/health responde 200 en desarrollo con mocks explícitos y sin
   secretos en el cuerpo.
2. Toda respuesta lleva X-Request-ID; si el cliente manda uno, se respeta
   (correlación de logs entre frontend y backend, §11.5).
3. Errores con la forma del contrato: 404 de ruta inexistente y 500 de
   excepción no manejada responden error.code/message + request_id, y el
   500 NO expone stacktrace (criterio 2 del issue).
4. APP_ENV=production con config incompleta falla al construir la app,
   listando TODAS las variables faltantes (criterio 3).
5. CORS deshabilitado por defecto; habilitado solo con orígenes.

Cómo se lee si no conocés TestClient: es un cliente HTTP "en memoria" que
llama a la app sin abrir puerto ni red — FastAPI lo incluye para tests.
`raise_server_exceptions=False` le ordena NO re-lanzar las excepciones del
servidor y devolver la respuesta 500 tal como la vería un cliente real.
"""

import pytest
from app.config import Configuracion, ConfiguracionIncompleta
from app.main import CABECERA_REQUEST_ID, crear_app
from fastapi.testclient import TestClient

# Marca del módulo completo: estos tests son de unidad (sin red, sin IO).
pytestmark = pytest.mark.unit


def config_desarrollo(**sobrescrituras) -> Configuracion:
    """Config de desarrollo con mocks explícitos, inyectable y aislada."""
    valores = {"app_env": "development", "mock_oci": True, "mock_gemini": True}
    valores.update(sobrescrituras)
    return Configuracion(**valores)


@pytest.fixture
def cliente() -> TestClient:
    """App construida con config de desarrollo + cliente de pruebas."""
    return TestClient(crear_app(config_desarrollo()))


def test_health_responde_200_sin_secretos(cliente):
    """Criterio 1: disponible en desarrollo con mocks; cuerpo mínimo."""
    respuesta = cliente.get("/api/health")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "ok"
    assert "version" in cuerpo
    # §7.1: sin secretos. Ningún valor de configuración puede colarse.
    texto = str(cuerpo).lower()
    for secreto_prohibido in ("placeholder", "api_key", "google", "oci_", "password"):
        assert secreto_prohibido not in texto


def test_toda_respuesta_lleva_request_id_generado(cliente):
    """Sin X-Request-ID del cliente, el middleware genera uno propio."""
    respuesta = cliente.get("/api/health")
    request_id = respuesta.headers.get(CABECERA_REQUEST_ID)
    assert request_id and request_id.startswith("req_")


def test_request_id_del_cliente_se_respeta(cliente):
    """El frontend puede correlacionar sus logs mandando el suyo."""
    respuesta = cliente.get("/api/health", headers={CABECERA_REQUEST_ID: "req_mio_123"})
    assert respuesta.headers[CABECERA_REQUEST_ID] == "req_mio_123"


def test_404_con_envoltorio_de_contrato(cliente):
    """Ruta inexistente: 404 con error.code NOT_FOUND + request_id (§7.3)."""
    respuesta = cliente.get("/api/no-existe")
    assert respuesta.status_code == 404
    cuerpo = respuesta.json()
    assert cuerpo["error"]["code"] == "NOT_FOUND"
    assert cuerpo["request_id"].startswith("req_")
    # La cabecera de trazabilidad también está en errores.
    assert CABECERA_REQUEST_ID in respuesta.headers


def test_excepcion_no_manejada_responde_500_estandar():
    """Criterio 2: 500 con envoltorio + request_id y SIN stacktrace interna."""
    # raise_server_exceptions=False: sin esto, TestClient RELANZA la excepción
    # del servidor en el test en vez de mostrar la respuesta 500 que vería
    # un cliente real (que es justo lo que este test quiere inspeccionar).
    cliente = TestClient(crear_app(config_desarrollo()), raise_server_exceptions=False)

    # Ruta de prueba que explota a propósito, registrada solo en esta app de
    # test (los decoradores de ruta son funciones: se usan directamente).
    def _explotar() -> None:
        1 / 0  # noqa: B018 - la división por cero ES el caso de prueba

    cliente.app.get("/api/_boom")(_explotar)
    respuesta = cliente.get("/api/_boom")
    assert respuesta.status_code == 500
    cuerpo = respuesta.json()
    assert cuerpo["error"]["code"] == "INTERNAL"
    assert cuerpo["request_id"].startswith("req_")
    # Nada de interior técnico: ni traceback ni tipo de excepción viajan.
    import json

    texto = json.dumps(cuerpo)
    for filtrado in ("ZeroDivisionError", "Traceback", 'File "', "divided by zero"):
        assert filtrado not in texto


def test_produccion_incompleta_falla_al_arrancar_con_mensaje_accionable():
    """Criterio 3: APP_ENV=production + placeholders => no arranca, y el error
    nombra CADA variable faltante de una vez (arreglable en un ciclo)."""
    with pytest.raises(ConfiguracionIncompleta) as info:
        # mock_oci/mock_gemini EXPLICITOS en False: pydantic-settings tambien
        # lee el entorno, y la CI publica MOCK_OCI=1 a nivel job; sin esto, la
        # construccion heredaria el mock y reventaria por "production no admite
        # mocks" ANTES de llegar a la validacion que este test quiere probar.
        crear_app(Configuracion(app_env="production", mock_oci=False, mock_gemini=False))
    mensaje = str(info.value)
    for variable in ("GOOGLE_API_KEY", "OCI_COMPARTMENT_ID", "OCI_CONFIG_FILE", "OCI_REGION"):
        assert variable in mensaje, f"el mensaje no nombra {variable}"
    # Accionable: indica dónde mirar y la salida de desarrollo.
    assert ".env.example" in mensaje
    assert "MOCK_OCI=1" in mensaje


def test_produccion_completa_arranca(tmp_path):
    """Camino positivo: producción con config real llega a construir la app.
    OCI_CONFIG_FILE debe apuntar a un archivo existente (la validación
    crítica lo exige), así que el test crea uno temporal."""
    archivo_credenciales = tmp_path / "config"
    archivo_credenciales.write_text("[DEFAULT]\nuser=falso\n", encoding="utf-8")
    app = crear_app(
        Configuracion(
            app_env="production",
            # Idem test anterior: explicitos para no heredar los mocks de la CI.
            mock_oci=False,
            mock_gemini=False,
            google_api_key="AIza_real_de_prueba",
            oci_compartment_id="ocid1.compartment.oc1..x",
            oci_region="us-ashburn-1",
            oci_config_file=str(archivo_credenciales),
        )
    )
    with TestClient(app) as cliente:
        assert cliente.get("/api/health").status_code == 200


def test_produccion_rechaza_mocks():
    """§8.2: MOCK_OCI=1 con APP_ENV=production es contradicción, no warning."""
    with pytest.raises(ValueError, match="no admite mocks"):
        crear_app(Configuracion(app_env="production", mock_oci=True))


def test_cors_deshabilitado_por_defecto(cliente):
    """Sin CORS_ORIGINS no hay cabeceras CORS en las respuestas."""
    respuesta = cliente.get("/api/health", headers={"Origin": "https://sitio-ajeno.example"})
    assert "access-control-allow-origin" not in respuesta.headers


def test_cors_habilitado_solo_con_origenes_configurados():
    """Con orígenes explícitos, el preflight OPTIONS responde con CORS."""
    app = crear_app(config_desarrollo(cors_origins="https://app.ejemplo.com"))
    with TestClient(app) as cliente:
        preflight = cliente.options(
            "/api/health",
            headers={
                "Origin": "https://app.ejemplo.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert preflight.headers.get("access-control-allow-origin") == "https://app.ejemplo.com"
        # Un origen NO listado no recibe permiso (§11.2: mínimos necesarios).
        respuesta_ajena = cliente.get("/api/health", headers={"Origin": "https://ajeno.example"})
        assert respuesta_ajena.headers.get("access-control-allow-origin") != "https://ajeno.example"
