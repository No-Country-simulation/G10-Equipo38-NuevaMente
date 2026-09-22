"""conftest.py RAÍZ del repo (issue #10): entorno de test y sesión de API.

Qué es un conftest.py: un archivo especial de pytest que se carga ANTES
que cualquier test del directorio (y subdirectorios). Sirve para dejar
listas, una sola vez, las condiciones bajo las que corre TODA la suite.

Responsabilidades de este archivo (las pide el issue):

1. ENTORNO DE TEST: fijar MOCK_OCI=1, APP_ENV=test y MOCK_GEMINI=1 antes
   de que ningún módulo del backend lea el entorno (§12.3: las pruebas
   ordinarias usan mocks explícitos, sin red ni credenciales). La selección
   explícita de integration_real exige ambos mocks desactivados por entorno.
2. SESIÓN DE API: fixture `api_cliente` con httpx.AsyncClient contra la
   app FastAPI SIN abrir puerto (transporte ASGI en memoria). Una dependencia
   ausente falla: los issues #07 y #10 ya están integrados.

El bucle de eventos asíncrono lo provee pytest-asyncio (configurado como
``asyncio_mode=auto`` en pytest.ini: cualquier test `async def` corre sin
decoradores extra).

Los fixtures de DOCUMENTOS (PDFs, chunks, casos factuales) viven en
backend/tests/conftest.py, junto a los tests que los consumen.
"""

from __future__ import annotations

import os

# --- 1) Entorno de test: ANTES de cualquier import de aplicación. ----------
# El orden importa: estos valores deben estar fijados cuando app.config o
# app.storage se importen por primera vez, porque leen el entorno al cargar.
import pytest


def pytest_configure(config):
    """La suite ordinaria no hereda accidentalmente la configuración de producción."""
    expresion = config.option.markexpr
    reales = "integration_real" in expresion and "not integration_real" not in expresion
    if reales:
        if any(os.environ.get(nombre) != "0" for nombre in ("MOCK_OCI", "MOCK_GEMINI")):
            raise pytest.UsageError("La suite integration_real exige MOCK_OCI=0 y MOCK_GEMINI=0 explícitos")
    else:
        os.environ.update(MOCK_OCI="1", MOCK_GEMINI="1")
    os.environ["APP_ENV"] = "ci" if os.environ.get("APP_ENV") == "ci" else "test"


@pytest.fixture
async def api_cliente(request):
    """Sesión HTTP contra la API en memoria (httpx + transporte ASGI).

    Para tests integration_mock: la app FastAPI se invoca directamente.
    """
    from app.config import Configuracion
    from app.main import crear_app
    from httpx import ASGITransport, AsyncClient

    if request.node.get_closest_marker("integration_real"):
        pytest.fail("integration_real no puede usar api_cliente con proveedores mock")
    # Config de test explícita: mocks activados, sin tocar el entorno.
    app = crear_app(Configuracion(app_env="test", mock_oci=True, mock_gemini=True))
    transporte = ASGITransport(app=app)
    async with AsyncClient(transport=transporte, base_url="http://test") as cliente:
        yield cliente
