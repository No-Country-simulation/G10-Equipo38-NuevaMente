"""Paquete raíz del backend de NuevaMente (servicio FastAPI).

Estructura de responsabilidades definida en decisiones_proyecto.md §15. Los
paquetes son esqueletos vacíos pero importables; el código llega con los
issues de cada carril (docs/guia-trabajo-equipo.md §2).

Archivos de este nivel:
- app/main.py: punto de entrada FastAPI (issue #07): factory crear_app(),
  GET /api/health, middleware request_id, handlers de error con el
  envoltorio del contrato y CORS deshabilitado por defecto.
- app/config.py: configuración validada al arranque con Pydantic Settings
  (issue #07, Apéndice A): producción incompleta no arranca; los mocks
  MOCK_OCI/MOCK_GEMINI solo valen en development/test/ci.

Verificación del esqueleto (issue #01): `python -c "import app"` desde
backend/ con un venv limpio de Python 3.11.
"""
