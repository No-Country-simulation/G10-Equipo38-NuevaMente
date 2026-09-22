"""Núcleo de dominio del backend, ajeno al transporte HTTP.

Subpaquetes (§15):
- rag/: pipeline RAG — ingesta, indexación y recuperación de evidencia.
- agents/: orquestación multi-agente con LangGraph (§5).
- faithfulness/: verificador de fidelidad y anclaje_fuente_score (§19).
- exports/: exportadores pedagógicos multiformato.
"""
