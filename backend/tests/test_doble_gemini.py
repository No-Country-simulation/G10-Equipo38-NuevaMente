"""Tests del doble de Gemini (issue #10).

Verifican las dos garantías que el doble promete (determinismo y
scriptabilidad) más las propiedades matemáticas que hacen a los embeddings
INDEXABLES por ChromaDB (criterio de aceptación 2 del issue): dimensión
configurable, norma unitaria, estabilidad ante re-llamadas y similitudes
coherentes (idéntico => 1, distinto => ~0).

La indexación REAL en Chroma se prueba en test_vectorstore_mock.py con
marca integration_mock, usando el mismo generador.
"""

import math

import pytest

pytestmark = pytest.mark.unit


async def test_generar_devuelve_las_respuestas_programadas_en_orden(doble_gemini):
    doble_gemini.programar_generacion("primera respuesta", "segunda respuesta")
    assert await doble_gemini.generar("prompt") == "primera respuesta"
    assert await doble_gemini.generar("prompt") == "segunda respuesta"
    # Agotadas las programadas, la respuesta delata el descuido (no inventa).
    assert "no programada" in await doble_gemini.generar("prompt")


async def test_verificar_afirmacion_es_conservador_por_defecto(doble_gemini):
    veredicto = await doble_gemini.verificar_afirmacion("afirmacion", "evidencia")
    assert veredicto["respaldada"] is False  # sin programar => no respalda

    doble_gemini.programar_veredicto(True, "la fuente lo dice")
    assert (await doble_gemini.verificar_afirmacion("a", "e")) == {
        "respaldada": True,
        "razon": "la fuente lo dice",
    }


async def test_contadores_de_llamadas(doble_gemini):
    """El presupuesto de llamadas de §7.5 se prueba contando, no adivinando."""
    doble_gemini.programar_generacion("x")
    await doble_gemini.generar("p")
    await doble_gemini.generar("p")
    await doble_gemini.embed(["a"])
    assert doble_gemini.llamadas_generacion == 2
    assert doble_gemini.llamadas_verificacion == 0
    assert doble_gemini.llamadas_embeddings == 1


async def test_embeddings_deterministas_y_de_dimension_configurable():
    from doubles.gemini import DobleGemini

    doble = DobleGemini(dimension_embeddings=16)
    primera = (await doble.embed(["una frase"]))[0]
    segunda = (await doble.embed(["una frase"]))[0]
    assert primera == segunda  # determinismo puro: misma entrada, mismo vector
    assert len(primera) == 16


async def test_embeddings_con_norma_unitaria(doble_gemini):
    """ChromaDB compara por producto punto con vectores normalizados."""
    vector = (await doble_gemini.embed(["texto cualquiera"]))[0]
    assert math.isclose(sum(c**2 for c in vector), 1.0, rel_tol=1e-9)


async def test_similitudes_coherentes(doble_gemini):
    """Idéntico => coseno 1; distintos => cerca de 0 (hashes independientes)."""

    def coseno(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b))  # normas 1: el producto ES el coseno

    v1a, v1b, v2 = (await doble_gemini.embed(["redes vcn", "redes vcn", "gobernanza de datos"]))[:3]
    assert math.isclose(coseno(v1a, v1b), 1.0, abs_tol=1e-12)
    assert abs(coseno(v1a, v2)) < 0.25  # en dim 768 el coseno de hashes ronda ±0.04
