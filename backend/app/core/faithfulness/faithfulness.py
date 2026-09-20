"""Verificador de fidelidad estilo Ragas (issue #24, referencia §19.1 y §19.2).

Qué mide este módulo: la CONSISTENCIA de un contenido generado con la
evidencia recuperada del documento fuente — el famoso
`anclaje_fuente_score`. Es el corazón anti-alucinación del proyecto: un
contenido que afirma cosas que la fuente no respalda NO puede aprobarse,
por más elocuente que suene.

El método (§19.1), en dos pasos:

1. DESCOMPOSICIÓN: el contenido se parte en AFIRMACIONES ATÓMICAS —
   oraciones que sostienen UN hecho cada una, con pronombres resueltos
   ("ella" → "la VCN") para que cada una se sostenga por sí sola.
2. JUICIO NLI: cada afirmación se contrasta contra el contexto (la
   evidencia recuperada) con un veredicto binario: respaldada o no, con
   motivo breve. El score es respaldadas / total.

Por qué la interfaz es INYECTADA (callables `descomponer` y `juzgar`):
los pasos 1 y 2 los hace un LLM en producción (el cliente de #13 con los
prompts de #22), pero los TESTS inyectan dobles deterministas (el doble
de Gemini de #10 y los 12 casos factuales de §12.2). Así el ALGORITMO de
salvaguardas — lo que este archivo aporta — queda probado sin red.

Salvaguardas que hacen a este verificador confiable (todas exigidas por
§19.1 y por los criterios del issue):

- DENOMINADOR = la lista ORIGINAL de afirmaciones. Si el juez devuelve
  menos juicios (o de más, duplicados, o con ids fuera de rango), la
  evaluación entera es INVÁLIDA (no_evaluable): nunca un score parcial
  con denominador achicado, que sería la forma silenciosa de inflar 1.0.
- Texto sin afirmaciones / salida vacía → score NULO + no_evaluable.
  "Una salida vacía no recibe 1.0": un contenido sin afirmaciones no es
  un contenido perfecto, es un contenido sin evaluar.
- Error/excepción del juez → FALLO TÉCNICO (estado fallo_tecnico): un
  proveedor caído no produce un veredicto factual. §19.3: "fallo técnico
  o no_evaluable con diagnóstico", jamás un falso aprobado.
- Pedagogía (§19.2): los DISTRRACTORES del quiz (deliberadamente falsos)
  no cuentan como afirmaciones educativas: se comprueban APARTE, y se
  espera que NO estén respaldados (un distractor respaldado por la fuente
  significa que el quiz tiene dos respuestas correctas: se reporta como
  hallazgo, no se promedia). Las ANALOGÍAS etiquetadas tampoco se juzgan
  como hechos literales: quien llama separa el texto factual del no
  factual, y este módulo lo registra como `no_juzgados_etiquetados`.

Sobre el score: mide consistencia con la evidencia RECUPERADA; no certifica
que la fuente sea verdadera ni completa (§19.1). La POLÍTICA de aprobación
(umbrales 0.70/0.85 de §19.3) NO vive acá: la decide el Critic (#28). Este
módulo solo entrega el número honesto y su desglose. Para que #28 no tenga
que recablear umbrales, `franja_de_aprobacion` traduce un score a la banda
de §19.3 — función pura, documentada, sin lógica de decisión.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

# --------------------------------------------------------------------------
# Modelos de datos del verificador
# --------------------------------------------------------------------------


class EstadoEvaluacion(str, Enum):
    """Cómo terminó la evaluación (§19.1).

    - EVALUABLE: hubo descomposición y juicios completos; el score es válido.
    - NO_EVALUABLE: sin afirmaciones que juzgar, o juicios inválidos
      (faltantes/duplicados/fuera de rango). Score NULO, nunca 1.0.
    - FALLO_TECNICO: el juez o el descompositor explotaron. Es un error de
      dependencia, NO un veredicto sobre el contenido (§19.3).
    """

    EVALUABLE = "evaluable"
    NO_EVALUABLE = "no_evaluable"
    FALLO_TECNICO = "fallo_tecnico"


@dataclass(frozen=True)
class Afirmacion:
    """Una afirmación atómica con id ESTABLE (§19.1: "identificador estable").

    El id viaja hacia el juez y vuelve en cada juicio: es lo que permite
    validar que el juez respondió TODAS y solo las que se le pidieron.
    """

    id: str
    texto: str


@dataclass(frozen=True)
class Juicio:
    """El veredicto del juez sobre UNA afirmación (§19.1: veredicto, motivo
    breve y referencias)."""

    id_afirmacion: str
    respaldada: bool
    motivo: str = ""
    referencias: tuple[str, ...] = ()


@dataclass
class ResultadoFidelidad:
    """Resultado completo de una evaluación de fidelidad.

    - `score`: respaldadas/total cuando EVALUABLE; None en cualquier otro
      caso. El llamador (Critic #28) decide con la banda de §19.3.
    - `diagnostico`: explicación humana del estado (por qué no_evaluable,
      qué hallazgos hubo). §19.4: el panel de calidad lo muestra tal cual.
    - `hallazgos`: problemas que NO invalidan el score pero que #28 debe
      ver (p. ej. un distractor de quiz respaldado por la fuente).
    """

    estado: EstadoEvaluacion
    score: float | None
    afirmaciones: list[Afirmacion] = field(default_factory=list)
    juicios: list[Juicio] = field(default_factory=list)
    total: int = 0
    respaldadas: int = 0
    diagnostico: str = ""
    hallazgos: list[str] = field(default_factory=list)
    # §19.2: segmentos etiquetados como analogía/escenario ficticio que NO
    # se juzgaron como hechos literales.
    no_juzgados_etiquetados: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Verificador
# --------------------------------------------------------------------------

# Firmas de los pasos inyectables (LLM real en producción, dobles en tests).
Descompositor = Callable[[str], list[Afirmacion]]
JuezNLI = Callable[[list[Afirmacion], str], list[Juicio]]


class VerificadorFidelidad:
    """Orquesta descomposición + juicio NLI con todas las salvaguardas."""

    def __init__(self, descomponer: Descompositor, juzgar: JuezNLI) -> None:
        self._descomponer = descomponer
        self._juzgar = juzgar

    def verificar(
        self,
        texto: str,
        contexto: str,
        distractores: list[str] | None = None,
        segmentos_etiquetados: list[str] | None = None,
    ) -> ResultadoFidelidad:
        """Evalúa `texto` contra `contexto` (la evidencia recuperada).

        - `distractores` (§19.2): opciones de quiz deliberadamente falsas.
          Se comprueban APARTE con expectativa de NO respaldo; no entran
          en el score.
        - `segmentos_etiquetados`: analogías/escenarios ficticios que el
          llamador marcó; no se juzgan (§19.2) y quedan registrados.
        """
        resultado = ResultadoFidelidad(estado=EstadoEvaluacion.NO_EVALUABLE, score=None)
        resultado.no_juzgados_etiquetados = list(segmentos_etiquetados or [])

        # ---- Paso 1: descomposición (falla => fallo técnico) ----
        try:
            afirmaciones = self._descomponer(texto)
        except Exception as error:  # noqa: BLE001 - la dependencia no dicta el veredicto
            resultado.estado = EstadoEvaluacion.FALLO_TECNICO
            resultado.diagnostico = f"El descompositor fallo: {error}"
            return resultado

        if not afirmaciones:
            # Sin afirmaciones no hay fidelidad que medir: score NULO
            # (§19.1: "una salida vacía no recibe 1.0").
            resultado.diagnostico = "El contenido no contiene afirmaciones evaluables."
            return resultado
        resultado.afirmaciones = list(afirmaciones)

        # ---- Paso 2: juicio NLI contra el contexto ----
        try:
            juicios = self._juzgar(afirmaciones, contexto)
        except Exception as error:  # noqa: BLE001
            resultado.estado = EstadoEvaluacion.FALLO_TECNICO
            resultado.diagnostico = f"El juez NLI fallo: {error}"
            return resultado

        # ---- Validación de completitud (§19.1: el corazón de la honestidad) ----
        esperados = {afirmacion.id for afirmacion in afirmaciones}
        recibidos = [juicio.id_afirmacion for juicio in juicios]
        faltantes = esperados - set(recibidos)
        duplicados = {identificador for identificador in recibidos if recibidos.count(identificador) > 1}
        fuera_de_rango = set(recibidos) - esperados
        if faltantes or duplicados or fuera_de_rango:
            partes = []
            if faltantes:
                partes.append(f"juicios faltantes: {sorted(faltantes)}")
            if duplicados:
                partes.append(f"juicios duplicados: {sorted(duplicados)}")
            if fuera_de_rango:
                partes.append(f"ids fuera de rango: {sorted(fuera_de_rango)}")
            resultado.diagnostico = (
                "Evaluacion invalida (" + "; ".join(partes) + "). El score se calcula sobre la "
                "lista ORIGINAL de afirmaciones; una respuesta incompleta del juez no produce score parcial."
            )
            return resultado

        resultado.juicios = list(juicios)
        resultado.total = len(afirmaciones)
        resultado.respaldadas = sum(1 for juicio in juicios if juicio.respaldada)
        resultado.score = resultado.respaldadas / resultado.total
        resultado.estado = EstadoEvaluacion.EVALUABLE
        resultado.diagnostico = f"{resultado.respaldadas} de {resultado.total} afirmaciones respaldadas."

        # ---- §19.2: distractores comprobados aparte ----
        for distracto in distractores or []:
            try:
                juicios_distractor = self._juzgar([Afirmacion(id="distractor", texto=distracto)], contexto)
            except Exception as error:  # noqa: BLE001
                # El fallo del juez sobre un distractor no invalida el score
                # principal, pero se reporta para que #28 lo vea.
                resultado.hallazgos.append(f"No se pudo comprobar un distractor: {error}")
                continue
            if juicios_distractor and juicios_distractor[0].respaldada:
                resultado.hallazgos.append(
                    f"Distractor RESPALDADO por la fuente ({distractor_corto(distracto)}): "
                    "el quiz tendria mas de una respuesta correcta."
                )
        return resultado


def distractor_corto(texto: str, largo: int = 60) -> str:
    """Recorta un distractor para los mensajes de hallazgo (legibilidad)."""
    return texto if len(texto) <= largo else texto[: largo - 1] + "…"


# --------------------------------------------------------------------------
# Banda de aprobación de §19.3 (la DECISIÓN es del Critic #28)
# --------------------------------------------------------------------------


class FranjaAprobacion(str, Enum):
    """Bandas de §19.3 para un score EVALUABLE.

    Atención (#28): 0.85 es "candidato a aprobación", NO aprobación: aún
    deben pasar todas las comprobaciones (citas válidas, cobertura,
    revisión visual). Y toda afirmación marcada como falsa debe corregirse
    aunque el promedio alcance.
    """

    REHACER = "rehacer"  # score < 0.70: nunca aprobar
    REVISAR = "revisar"  # 0.70 <= score < 0.85: feedback y reintento si quedan
    CANDIDATO = "candidato"  # 0.85 <= score <= 1.00: sigue la rúbrica completa


def franja_de_aprobacion(score: float) -> FranjaAprobacion:
    """Traduce un score evaluabile a su banda de §19.3 (función pura)."""
    if score < 0.70:
        return FranjaAprobacion.REHACER
    if score < 0.85:
        return FranjaAprobacion.REVISAR
    return FranjaAprobacion.CANDIDATO
