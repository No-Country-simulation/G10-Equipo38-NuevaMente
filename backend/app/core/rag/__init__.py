"""Pipeline RAG (carril RAG): ingesta, indexación y recuperación de evidencia.

Contrato compartido del carril: documento, chunk, evidencia y filtros de
acceso (docs/guia-trabajo-equipo.md §2).

Módulos:
- parser.py (issue #11, implementado): ingesta de PDF/MD/TXT y texto pegado
  con validación de firma real, límites de §4.1 configurables, cobertura
  informe, páginas visuales derivadas al contrato de #30 y códigos de
  rechazo estables. Consumido por los endpoints de documentos (#19).
- chunker.py (issue #12, implementado): segmentación estructural 750/120,
  techo de 825 tokens incluyendo contexto, citas e IDs aislados por espacio.
- tokenizer.py: BPE cl100k_base local y versionado; no requiere red.
- embeddings.py: cliente de embeddings de Gemini (issue #13).
- vectorstore.py: interfaz ChromaDB con aislamiento por espacio (#17).
- retriever.py: búsqueda MMR con presupuesto de evidencia (issue #18).
"""
