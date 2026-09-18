"""Gestión de trabajos de generación (carril API): cola, estados, deadline,
cancelación e idempotencia.

Módulo previsto (§15): manager.py (issue #20).

Decisiones operativas del Apéndice A: GLOBAL_HEAVY_JOB_CONCURRENCY=1 (un solo
escritor pesado, §14.2), MAX_QUEUED_JOBS=5 y
GENERATION_DEADLINE_SECONDS=300. Los estados son los de JobStatus (§16.1):
queued/running/completed/rejected_quality/failed/cancelled. Repetir una
petición no duplica generación ni progreso (§12.2 criterio 11) y los trabajos
interrumpidos se identifican como fallidos, no completados (§14.2).
"""
