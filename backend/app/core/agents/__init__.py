"""Orquestación multi-agente con LangGraph (carril AGT, §5).

Contrato compartido del carril: estado del grafo, borrador tipado, evaluación
y resultado (docs/guia-trabajo-equipo.md §2).

Módulos previstos (§15):
- prompts.py: biblioteca de prompts por perfil/formato/nicho/idioma (#22).
- writer.py: Writer con salida tipada y citas (issue #23).
- researcher.py: consultas temáticas y cobertura (issue #27).
- critic.py: rúbrica pedagógica y política de aprobación (issue #28).
- supervisor.py: Supervisor y estado compartido del grafo (issue #21).
- graph.py: StateGraph completo con límite de 3 intentos (issue #29).
- finalizer.py: nodo Finalizer.

Límite operativo: MAX_GENERATION_ATTEMPTS=3 (Apéndice A); al agotar intentos
el trabajo se marca rejected_quality, nunca aprobado con evaluador vacío o
caído (§12.2 criterio 7).
"""
