"""Verificador de fidelidad anti-alucinación (§19): anclaje_fuente_score.

Módulo previsto (§15): faithfulness.py — implementación propia de la
metodología Ragas (issue #24): descomposición del contenido generado en
afirmaciones atómicas y verificación NLI contra la evidencia recuperada del
documento fuente.

Umbral de aprobación: score >= 0.85 (§12.2 y README); por debajo se activa el
ciclo de retroalimentación hacia el Writer con un máximo de 3 intentos.
"""
