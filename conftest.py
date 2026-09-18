"""conftest.py RAÍZ del repo (issue #10): entorno de test y sesión de API.

Qué es un conftest.py: un archivo especial de pytest que se carga ANTES
que cualquier test del directorio (y subdirectorios). Sirve para dejar
listas, una sola vez, las condiciones bajo las que corre TODA la suite.

Responsabilidades de este archivo (las pide el issue):

1. ENTORNO DE TEST: fijar MOCK_OCI=1, APP_ENV=test y MOCK_GEMINI=1 antes
   de que ningún módulo del backend lea el entorno (§12.3: las pruebas
   ordinarias usan mocks explícitos, sin red ni credenciales). Se usa
   ``setdefault`` para no pisar valores que la CI ya haya fijado.
2. SESIÓN DE API: fixture `api_cliente` con httpx.AsyncClient contra la
   app FastAPI SIN abrir puerto (transporte ASGI en memoria). Se degrada
   con SKIP si el esqueleto FastAPI todavía no está mergeado (PR del
   issue #07): así este PR puede integrarse en paralelo sin romper la CI.

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
# Se respeta un valor PREEXISTENTE y no vacío (la CI publica APP_ENV=ci desde
# el issue #02); un valor vacío cuenta como no definido, porque una variable
# en blanco es un descuido del entorno, no una decisión que haya que respetar.
for _variable, _valor in (("MOCK_OCI", "1"), ("MOCK_GEMINI", "1"), ("APP_ENV", "test")):
    if not os.environ.get(_variable):
        os.environ[_variable] = _valor

import pytest  # noqa: E402 (el import va deliberadamente después del entorno)


@pytest.fixture
async def api_cliente():
    """Sesión HTTP contra la API en memoria (httpx + transporte ASGI).

    Marca integration_mock porque ejercita el stack HTTP completo, aunque
    sin red: la app FastAPI se invoca directamente. Si el esqueleto del
    issue #07 aún no está en la rama, el fixture SKIPPEA (no falla): la
    infraestructura queda lista y se activa al mergear.
    """
    pytest.importorskip("app.main", reason="requiere el esqueleto FastAPI del issue #07")
    from app.config import Configuracion
    from app.main import crear_app
    from httpx import ASGITransport, AsyncClient

    # Config de test explícita: mocks activados, sin tocar el entorno.
    app = crear_app(Configuracion(app_env="test", mock_oci=True, mock_gemini=True))
    transporte = ASGITransport(app=app)
    async with AsyncClient(transport=transporte, base_url="http://test") as cliente:
        yield cliente
