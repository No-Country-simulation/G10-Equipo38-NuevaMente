"""Tests del verificador de fidelidad (issue #24).

Verificación exacta del issue: `pytest backend/tests/test_faithfulness.py`
usando como suite mínima los fixtures factuales de §12.2 (issue #10).

Criterio por criterio:

1. Texto 100% respaldado por el contexto → score 1.0 con todas respaldadas.
2. Una cifra inventada entre 10 respaldadas baja el score Y marca esa
   afirmación con motivo (score 10/11 ≈ 0.909).
3. Juez que devuelve lista INCOMPLETA de juicios → no_evaluable (nunca
   score parcial). Ídem duplicados y ids fuera de rango.
4. Los casos de fixture §12.2 pasan con los veredictos esperados.

Además (salvaguardas de §19.1): salida vacía → no_evaluable con score
None (jamás 1.0), denominador cero, excepción del juez → fallo_técnico,
distractores comprobados aparte (§19.2, con hallazgo si un distractor
resulta respaldado), analogías etiquetadas no juzgadas, y las bandas de
§19.3 para el Critic (#28).

Cómo leer los dobles: `descomponer` y `juzgar` son funciones que el test
DEFINE, con respuestas de libreta. Así se prueban las salvaguardas del
verificador (lo que este issue aporta) sin LLM ni red; en producción, #13
+#22 inyectan el cliente real.
"""

import pytest
from app.core.faithfulness.faithfulness import (
    Afirmacion,
    EstadoEvaluacion,
    FranjaAprobacion,
    Juicio,
    VerificadorFidelidad,
    franja_de_aprobacion,
)
from fixtures.factuales import CASOS_FACTUALES

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "afirmaciones",
    [None, ["texto"], [Afirmacion("", "A")], [Afirmacion("a", "A"), Afirmacion("a", "B")], [Afirmacion("a", " ")]],
)
def test_descomposicion_malformada_no_produce_score(afirmaciones):
    resultado = VerificadorFidelidad(lambda t: afirmaciones, lambda a, c: []).verificar("contenido", "fuente")
    assert resultado.estado is EstadoEvaluacion.NO_EVALUABLE
    assert resultado.score is None


@pytest.mark.parametrize("juicios", [None, ["texto"], [Juicio("a", "false")], [Juicio("a", 1)]])
def test_juicios_exigen_booleanos_reales(juicios):
    resultado = VerificadorFidelidad(lambda t: [Afirmacion("a", "A")], lambda a, c: juicios).verificar("A", "A")
    assert resultado.score is None


def test_juez_no_puede_modificar_el_denominador():
    def juez(afirmaciones, contexto):
        afirmaciones.pop()
        return [Juicio("a", True)]

    verificador = VerificadorFidelidad(lambda t: [Afirmacion("a", "A"), Afirmacion("b", "B")], juez)
    resultado = verificador.verificar("A y B", "A")
    assert resultado.score is None
    assert resultado.total == 2
    assert len(resultado.afirmaciones) == 2


def test_exclusiones_se_aplican_antes_de_descomponer():
    entradas = []

    def descomponer(texto):
        entradas.append(texto)
        return [Afirmacion("a", texto.strip())]

    def juzgar(afirmaciones, contexto):
        return [Juicio(a.id, a.id != "distractor") for a in afirmaciones]

    resultado = VerificadorFidelidad(descomponer, juzgar).verificar(
        "Hecho real.\nOpción falsa.\nAnalogía ficticia.",
        "Hecho real.",
        distractores=["Opción falsa."],
        segmentos_etiquetados=["Analogía ficticia."],
    )
    assert entradas[0].strip() == "Hecho real."
    assert resultado.score == 1.0 and not resultado.hallazgos


@pytest.mark.parametrize("salida", [[], [Juicio("id_inventado", False)], [Juicio("distractor", "false")]])
def test_distractor_sin_veredicto_valido_bloquea_aprobacion(salida):
    def juez(afirmaciones, contexto):
        return salida if afirmaciones[0].id == "distractor" else [Juicio("a", True)]

    resultado = VerificadorFidelidad(lambda t: [Afirmacion("a", "A")], juez).verificar("A", "A", distractores=["B"])
    assert resultado.score == 1.0  # La comprobación aparte no altera el denominador.
    assert resultado.hallazgos


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1, True, None])
def test_franja_no_acepta_scores_invalidos(score):
    with pytest.raises(ValueError):
        franja_de_aprobacion(score)


@pytest.mark.parametrize("texto,contexto", [("", "fuente"), ("contenido", " ")])
def test_no_se_evaluan_entradas_vacias(texto, contexto):
    def no_llamar(*args):
        pytest.fail("No debe consumir una llamada sin entrada o evidencia")

    assert VerificadorFidelidad(no_llamar, no_llamar).verificar(texto, contexto).score is None


CONTEXTO_VCN = (
    "Una VCN es la red privada que aísla los recursos en OCI. Las subredes públicas enrutan "
    "0.0.0.0/0 hacia el Internet Gateway. El NAT Gateway da salida a Internet sin exponer IP públicas."
)


def descompositor_de(textos: list[str]):
    """Doble descompositor: cada texto pasa a ser una afirmación atómica."""

    def _descomponer(contenido: str) -> list[Afirmacion]:
        return [Afirmacion(id=f"af_{indice}", texto=texto) for indice, texto in enumerate(textos)]

    return _descomponer


def juez_de_libreta(libreta: dict[str, Juicio]):
    """Doble juez NLI: responde con la libreta por id de afirmación."""

    def _juzgar(afirmaciones, contexto):
        return [libreta[afirmacion.id] for afirmacion in afirmaciones if afirmacion.id in libreta]

    return _juzgar


# ---------------------------------------------------------------------------
# Criterio 1: todo respaldado → 1.0
# ---------------------------------------------------------------------------


def test_texto_totalmente_respaldado_da_score_1():
    afirmaciones = ["La VCN es una red privada.", "El NAT evita exponer IP públicas."]
    verificador = VerificadorFidelidad(
        descompositor_de(afirmaciones),
        juez_de_libreta(
            {
                "af_0": Juicio("af_0", True, "la fuente lo afirma"),
                "af_1": Juicio("af_1", True, "la fuente lo afirma"),
            }
        ),
    )
    resultado = verificador.verificar("contenido", CONTEXTO_VCN)

    assert resultado.estado is EstadoEvaluacion.EVALUABLE
    assert resultado.score == 1.0
    assert resultado.respaldadas == resultado.total == 2
    assert all(juicio.respaldada for juicio in resultado.juicios)


# ---------------------------------------------------------------------------
# Criterio 2: una cifra inventada entre 10 respaldadas
# ---------------------------------------------------------------------------


def test_una_cifra_inventada_entre_diez_respaldadas_baja_el_score_y_marca_el_motivo():
    respaldadas = [f"Afirmación respaldada número {indice}." for indice in range(10)]
    inventada = "El paquete educativo puede pesar hasta 50 MB."  # cifra que la fuente no contiene

    libreta = {f"af_{indice}": Juicio(f"af_{indice}", True, "respaldo en la fuente") for indice in range(10)}
    libreta["af_10"] = Juicio("af_10", False, "cifra ausente de la fuente (número inventado)")

    verificador = VerificadorFidelidad(descompositor_de(respaldadas + [inventada]), juez_de_libreta(libreta))
    resultado = verificador.verificar("contenido", CONTEXTO_VCN)

    assert resultado.estado is EstadoEvaluacion.EVALUABLE
    assert resultado.score == pytest.approx(10 / 11)
    assert resultado.total == 11 and resultado.respaldadas == 10
    # La afirmación inventada queda individualizada con su motivo (criterio).
    juicio_inventado = [juicio for juicio in resultado.juicios if not juicio.respaldada]
    assert len(juicio_inventado) == 1
    assert "número inventado" in juicio_inventado[0].motivo
    assert juicio_inventado[0].id_afirmacion == "af_10"


# ---------------------------------------------------------------------------
# Criterio 3: juicios incompletos/inválidos → no_evaluable (nunca parcial)
# ---------------------------------------------------------------------------


def test_juez_con_lista_incompleta_invalida_la_evaluacion():
    """El juez "se olvida" de af_1: score NULO, no 1.0 con la mitad."""
    verificador = VerificadorFidelidad(
        descompositor_de(["Afirmación uno.", "Afirmación dos."]),
        juez_de_libreta({"af_0": Juicio("af_0", True)}),  # falta af_1
    )
    resultado = verificador.verificar("contenido", CONTEXTO_VCN)

    assert resultado.estado is EstadoEvaluacion.NO_EVALUABLE
    assert resultado.score is None
    assert "faltantes: ['af_1']" in resultado.diagnostico
    assert "ORIGINAL" in resultado.diagnostico  # explica la regla del denominador


def test_juez_con_duplicados_o_ids_fueras_de_rango_invalida():
    textos = ["A.", "B."]
    juicios_duplicados = [Juicio("af_0", True), Juicio("af_0", True)]
    verificador = VerificadorFidelidad(descompositor_de(textos), lambda afirmaciones, contexto: juicios_duplicados)
    resultado = verificador.verificar("c", CONTEXTO_VCN)
    assert resultado.estado is EstadoEvaluacion.NO_EVALUABLE
    assert "duplicados" in resultado.diagnostico

    juicios_raros = [Juicio("af_0", True), Juicio("af_1", True), Juicio("af_inventado_99", True)]
    verificador = VerificadorFidelidad(descompositor_de(textos), lambda afirmaciones, contexto: juicios_raros)
    resultado = verificador.verificar("c", CONTEXTO_VCN)
    assert resultado.estado is EstadoEvaluacion.NO_EVALUABLE
    assert "fuera de rango" in resultado.diagnostico


def test_salida_vacia_del_descompositor_no_recibe_1():
    """§19.1 literal: contenido sin afirmaciones → no_evaluable, score None."""
    verificador = VerificadorFidelidad(lambda texto: [], lambda afirmaciones, contexto: [])
    resultado = verificador.verificar("una analogía vacía", CONTEXTO_VCN)
    assert resultado.estado is EstadoEvaluacion.NO_EVALUABLE
    assert resultado.score is None


# ---------------------------------------------------------------------------
# Criterio 4: la suite de fixtures §12.2 pasa con los veredictos esperados
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("caso", CASOS_FACTUALES, ids=[caso["id"] for caso in CASOS_FACTUALES])
def test_los_casos_factuales_pasan_con_los_veredictos_esperados(caso):
    """Cada caso NLI de §12.2: descomposición de 1 afirmación, juez con la
    expectativa del fixture, y el verificador respeta el veredicto."""
    verificador = VerificadorFidelidad(
        descompositor_de([caso["texto_generado"]]),
        juez_de_libreta({"af_0": Juicio("af_0", caso["esperado"]["respaldada"], caso["esperado"]["razon"])}),
    )
    resultado = verificador.verificar(caso["texto_generado"], caso["evidencia"])

    assert resultado.estado is EstadoEvaluacion.EVALUABLE
    score_esperado = 1.0 if caso["esperado"]["respaldada"] else 0.0
    assert resultado.score == score_esperado
    assert resultado.juicios[0].motivo == caso["esperado"]["razon"]


# ---------------------------------------------------------------------------
# Fallo técnico y manejo pedagógico (§19.2)
# ---------------------------------------------------------------------------


def test_excepcion_del_juez_es_fallo_tecnico_no_veredicto():
    def juez_roto(afirmaciones, contexto):
        raise RuntimeError("proveedor caído")

    verificador = VerificadorFidelidad(descompositor_de(["A."]), juez_roto)
    resultado = verificador.verificar("c", CONTEXTO_VCN)
    assert resultado.estado is EstadoEvaluacion.FALLO_TECNICO
    assert resultado.score is None
    assert "juez NLI" in resultado.diagnostico
    assert "proveedor caído" not in resultado.diagnostico


def test_excepcion_del_descompositor_es_fallo_tecnico():
    def descompositor_roto(texto):
        raise RuntimeError("timeout del modelo")

    verificador = VerificadorFidelidad(descompositor_roto, lambda a, c: [])
    resultado = verificador.verificar("c", CONTEXTO_VCN)
    assert resultado.estado is EstadoEvaluacion.FALLO_TECNICO


def test_distractores_se_comprueban_aparte_y_no_entran_en_el_score():
    """§19.2: un distractor NO respaldado es lo correcto (opción falsa
    verificada); no altera el score. Un distractor RESPALDADO es hallazgo:
    el quiz tendría dos respuestas correctas."""
    verificador = VerificadorFidelidad(
        descompositor_de(["Afirmación respaldada."]),
        juez_de_libreta({"af_0": Juicio("af_0", True), "distractor": Juicio("distractor", False)}),
    )
    resultado = verificador.verificar("c", CONTEXTO_VCN, distractores=["El tamaño del bucket define la red."])
    assert resultado.score == 1.0  # el distractor no cuenta en el denominador
    assert resultado.hallazgos == []

    verificador_con_distractor_valido = VerificadorFidelidad(
        descompositor_de(["Afirmación respaldada."]),
        juez_de_libreta({"af_0": Juicio("af_0", True), "distractor": Juicio("distractor", True)}),
    )
    resultado = verificador_con_distractor_valido.verificar(
        "c", CONTEXTO_VCN, distractores=["La VCN es una red privada."]
    )
    assert resultado.score == 1.0  # sigue sin afectar el score...
    # El mensaje del hallazgo habla de "mas de una respuesta correcta" (la
    # formulacion real del problema), no de un numero fijo.
    assert any("respuesta correcta" in hallazgo for hallazgo in resultado.hallazgos)  # ...pero se reporta


def test_analogias_etiquetadas_no_se_juzgan_como_hechos():
    """§19.2: la analogía marcada queda registrada y fuera del juicio."""
    verificador = VerificadorFidelidad(
        descompositor_de(["La VCN es una red privada."]),
        juez_de_libreta({"af_0": Juicio("af_0", True)}),
    )
    resultado = verificador.verificar(
        "c",
        CONTEXTO_VCN,
        segmentos_etiquetados=["Imaginá la VCN como un barrio cerrado de una ciudad."],
    )
    assert resultado.score == 1.0
    assert resultado.no_juzgados_etiquetados == ["Imaginá la VCN como un barrio cerrado de una ciudad."]


# ---------------------------------------------------------------------------
# Bandas de §19.3 (insumo puro para el Critic #28)
# ---------------------------------------------------------------------------


def test_franjas_de_aprobacion_19_3():
    assert franja_de_aprobacion(0.0) is FranjaAprobacion.REHACER
    assert franja_de_aprobacion(0.69) is FranjaAprobacion.REHACER
    assert franja_de_aprobacion(0.70) is FranjaAprobacion.REVISAR
    assert franja_de_aprobacion(0.849) is FranjaAprobacion.REVISAR
    assert franja_de_aprobacion(0.85) is FranjaAprobacion.CANDIDATO
    assert franja_de_aprobacion(1.0) is FranjaAprobacion.CANDIDATO
