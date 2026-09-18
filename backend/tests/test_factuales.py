"""Tests de los casos factuales versionados (issue #10, §12.2).

Los casos son DATOS de prueba del verificador de fidelidad (#24): este
test garantiza que están completos, categorizados y con expectativa
explícita, para que el consumidor futuro no encuentre sorpresas.
"""

import pytest
from fixtures.factuales import CASOS_FACTUALES, CATEGORIAS_FACTUALES

pytestmark = pytest.mark.unit


def test_todas_las_categorias_de_12_2_tienen_casos():
    """§12.2 exige: negaciones, unidades, números inventados, contradicciones,
    ausencia de contexto e interpretación visual."""
    presentes = {caso["categoria"] for caso in CASOS_FACTUALES}
    assert presentes == set(CATEGORIAS_FACTUALES)
    # Al menos dos casos por categoría (respaldo y rechazo cruzados).
    for categoria in CATEGORIAS_FACTUALES:
        casos = [c for c in CASOS_FACTUALES if c["categoria"] == categoria]
        assert len(casos) >= 2, f"categoria {categoria} con pocos casos"


def test_estructura_de_cada_caso():
    for caso in CASOS_FACTUALES:
        assert set(caso) == {"id", "categoria", "texto_generado", "evidencia", "esperado"}, caso["id"]
        assert caso["texto_generado"].strip() and caso["evidencia"].strip()
        assert isinstance(caso["esperado"]["respaldada"], bool)
        assert caso["esperado"]["razon"].strip(), f"{caso['id']}: toda expectativa necesita razón"


def test_ids_unicos_y_hay_respaldadas_y_no_respaldadas():
    ids = [caso["id"] for caso in CASOS_FACTUALES]
    assert len(set(ids)) == len(ids)
    respaldadas = [c for c in CASOS_FACTUALES if c["esperado"]["respaldada"]]
    no_respaldadas = [c for c in CASOS_FACTUALES if not c["esperado"]["respaldada"]]
    assert respaldadas and no_respaldadas  # el juez se prueba en AMBOS sentidos


@pytest.mark.parametrize("caso", CASOS_FACTUALES, ids=[c["id"] for c in CASOS_FACTUALES])
async def test_el_doble_puede_juzgar_cada_caso(caso, doble_gemini):
    """Integración mínima doble+datos: programando el veredicto esperado,
    el verificador futuro tendrá el insumo listo para cada caso."""
    doble_gemini.programar_veredicto(caso["esperado"]["respaldada"], caso["esperado"]["razon"])
    veredicto = await doble_gemini.verificar_afirmacion(caso["texto_generado"], caso["evidencia"])
    assert veredicto["respaldada"] == caso["esperado"]["respaldada"]
