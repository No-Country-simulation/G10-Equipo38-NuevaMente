"""Verificador de fidelidad anti-alucinación (§19): anclaje_fuente_score.

faithfulness.py (issue #24, implementado): verificador propio de la
metodología Ragas. Descomposición en afirmaciones atómicas + juicio NLI
contra la evidencia, con las salvaguardas de §19.1: denominador = lista
ORIGINAL (juicios faltantes/duplicados/fuera de rango invalidan), salida
vacía → no_evaluable con score nulo, error del juez → fallo técnico.
Distractores de quiz y analogías manejados según §19.2. Los pasos LLM se
INYECTAN (cliente real de #13+#22; dobles y los 12 casos factuales de #10
en tests). franja_de_aprobacion expone las bandas 0.70/0.85 de §19.3 para
el Critic (#28), que es quien decide.

Umbral de aprobación: score >= 0.85 (§12.2 y README); por debajo se activa el
ciclo de retroalimentación hacia el Writer con un máximo de 3 intentos.
"""
