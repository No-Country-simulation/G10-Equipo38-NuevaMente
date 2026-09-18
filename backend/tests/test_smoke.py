"""Smoke test de importacion (issue #02).

Por que existe: pytest devuelve el codigo de salida 5 cuando no encuentra
NINGUN test, y la CI trataria eso como fallo. Mientras los issues van
llenando los paquetes de codigo real, esta suite garantiza que siempre haya
al menos un test verdadero corriendo (issue #02: "evitar exit code 5 de
pytest sin tests, sin ocultar fallos").

Que verifica: que TODOS los paquetes del esqueleto (creados en el issue #01
segun la seccion 15 de decisiones_proyecto.md) sigan importables desde la
raiz, gracias al `pythonpath` definido en pytest.ini.

Por que no oculta fallos: importlib.import_module lanza una excepcion con
traceback completo si un paquete dejo de existir, se movio, o contiene un
error de sintaxis — el test muere con el mensaje exacto, no con un "pass"
silencioso.
"""

import importlib

# Mapa del monorepo: cada entrada es un paquete que el issue #01 creo y que
# issues futuros llenaran (ver el docstring de cada __init__.py para saber
# que le toca a cada uno). Si se agrega un paquete nuevo al arbol de §15,
# debe agregarse aqui: este test es el guardian de "vacios pero importables".
PAQUETES_BACKEND = [
    "app",
    "app.api",
    "app.api.routes",
    "app.core",
    "app.core.rag",
    "app.core.agents",
    "app.core.faithfulness",
    "app.core.exports",
    "app.storage",
    "app.schemas",
    "app.jobs",
    "app.session",
]

# Los paquetes del frontend viven sueltos en frontend/ (components, i18n,
# pages): Streamlit no exige un paquete unico, por eso importan por nombre.
PAQUETES_FRONTEND = [
    "components",
    "i18n",
    "pages",
]


def test_paquetes_del_backend_importables():
    """Cada paquete de backend/ debe poder importarse como en produccion."""
    for nombre in PAQUETES_BACKEND:
        # Si el import falla, import_module lanza y pytest muestra el error
        # exacto (modulo faltante, sintaxis rota, etc.). No hace falta assert.
        importlib.import_module(nombre)


def test_paquetes_del_frontend_importables():
    """Cada paquete de frontend/ debe poder importarse sin dependencias externas.

    Nota: hoy los paquetes estan vacios, asi que importan gratis. Cuando el
    issue #15 anada Streamlit, este test obliga a que las dependencias del
    frontend esten instaladas tambien en quien corre la suite — si eso
    molesta, el equipo podra mover esta parte a un marcador `frontend`.
    """
    for nombre in PAQUETES_FRONTEND:
        importlib.import_module(nombre)
