"""Pipeline RAG (carril RAG): ingesta, indexación y recuperación de evidencia.

Contrato compartido del carril: documento, chunk, evidencia y filtros de
acceso (docs/guia-trabajo-equipo.md §2).

Módulos:
- parser.py (issue #11, implementado): ingesta de PDF/MD/TXT y texto pegado
  con validación de firma real, límites de §4.1 configurables, cobertura
  informe, páginas visuales derivadas al contrato de #30 y códigos de
  rechazo estables. Consumido por los endpoints de documentos (#19).
- chunker.py: segmentación estructural medida en tokens con chunk_id
  estable (issue #12).
- embeddings.py: cliente de embeddings de Gemini (issue #13).
- vectorstore.py: interfaz ChromaDB con aislamiento por espacio (#17).
- retriever.py: búsqueda MMR con presupuesto de evidencia (issue #18).
"""
