"""Tests del sincronizador de dependencias de issues en Markdown.

Verifica que las casillas de dependencias hacia issues se marquen o desmarquen
correctamente según el estado de cierre del issue referenciado, y que las
casillas de criterios de aceptación u otras tareas permanezcan 100% intactas.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Asegurar que el directorio de scripts esté en el path de importación
DIRECTORIO_SCRIPTS = Path(__file__).resolve().parents[2] / ".github" / "scripts"
if str(DIRECTORIO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(DIRECTORIO_SCRIPTS))

from sync_issue_dependencies import sync_markdown_checkboxes  # noqa: E402

pytestmark = pytest.mark.unit


def test_marca_issue_cerrado_como_completado():
    """Un issue cerrado pasa de `- [ ] #10` a `- [x] #10`."""
    cuerpo = (
        "### ⛔ Bloqueado por (Predecesoras requeridas):\n"
        "- [x] #3 - Contratos compartidos v1\n"
        "- [ ] #10 - Infraestructura de tests: conftest, fixtures y doble de Gemini\n"
    )
    cerrados = {3, 10}

    nuevo_cuerpo, modificado = sync_markdown_checkboxes(cuerpo, cerrados)

    assert modificado is True
    assert "- [x] #10 - Infraestructura de tests: conftest, fixtures y doble de Gemini" in nuevo_cuerpo
    assert "- [x] #3 - Contratos compartidos v1" in nuevo_cuerpo


def test_desmarca_issue_reabierto():
    """Si un issue fue reabierto, pasa de `- [x] #10` a `- [ ] #10`."""
    cuerpo = "### ⛔ Bloqueado por:\n- [x] #10 - Infraestructura de tests\n"
    cerrados = {3}  # 10 ya no está cerrado

    nuevo_cuerpo, modificado = sync_markdown_checkboxes(cuerpo, cerrados)

    assert modificado is True
    assert "- [ ] #10 - Infraestructura de tests" in nuevo_cuerpo


def test_ignora_criterios_de_aceptacion_y_tareas_generales():
    """Las casillas que no sean referencias directas a issues NUNCA deben modificarse."""
    cuerpo = (
        "### ⛔ Bloqueado por:\n"
        "- [ ] #10 - Infraestructura de tests\n"
        "\n"
        "**Criterios de aceptación**:\n"
        "- [ ] Los 3 documentos de `Issue 05` se parsean con metadatos de página/sección.\n"
        "- [ ] PDF cifrado, archivo corrupto y archivo de 25 MB son rechazados.\n"
        "- [x] Alguna tarea completada manualmente que no es issue.\n"
    )
    cerrados = {10}

    nuevo_cuerpo, modificado = sync_markdown_checkboxes(cuerpo, cerrados)

    assert modificado is True
    assert "- [x] #10 - Infraestructura de tests" in nuevo_cuerpo
    # Verificar que los criterios de aceptación no fueron tocados
    assert "- [ ] Los 3 documentos de `Issue 05` se parsean con metadatos de página/sección." in nuevo_cuerpo
    assert "- [ ] PDF cifrado, archivo corrupto y archivo de 25 MB son rechazados." in nuevo_cuerpo
    assert "- [x] Alguna tarea completada manualmente que no es issue." in nuevo_cuerpo


def test_idempotencia_sin_cambios_necesarios():
    """Si todo está al día, no se reportan modificaciones."""
    cuerpo = "### ⛔ Bloqueado por:\n- [x] #3 - Contratos compartidos v1\n- [ ] #11 - Parser de documentos\n"
    cerrados = {3}

    nuevo_cuerpo, modificado = sync_markdown_checkboxes(cuerpo, cerrados)

    assert modificado is False
    assert nuevo_cuerpo == cuerpo


def test_manejo_cuerpo_vacio():
    """Un cuerpo vacío o None no debe lanzar excepción."""
    assert sync_markdown_checkboxes("", {1, 2}) == ("", False)
