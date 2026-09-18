"""Paquete raíz del backend de NuevaMente (servicio FastAPI).

Estructura de responsabilidades definida en decisiones_proyecto.md §15. Los
paquetes son esqueletos vacíos pero importables; el código llega con los
issues de cada carril (docs/guia-trabajo-equipo.md §2).

Archivos previstos a este nivel (no creados aún):
- app/main.py: punto de entrada FastAPI (issue #07).
- app/config.py: configuración validada desde variables de entorno, con los
  placeholders del Apéndice A de decisiones_proyecto.md (issue #07).

Verificación del esqueleto (issue #01): `python -c "import app"` desde
backend/ con un venv limpio de Python 3.11.
"""
