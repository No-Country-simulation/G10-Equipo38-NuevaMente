"""Pipeline RAG (carril RAG): ingesta, indexación y recuperación de evidencia.

Contrato compartido del carril: documento, chunk, evidencia y filtros de
acceso (docs/guia-trabajo-equipo.md §2).

Módulos previstos (§15):
- parser.py: extracción de texto de PDF/MD/TXT con validación de firma real,
  límites y timeout (issue #11; seguridad de §11.3).
- chunker.py: segmentación estructural medida en tokens con chunk_id
  estable (issue #12).
- embeddings.py: cliente de embeddings de Gemini (issue #13).
- vectorstore.py: interfaz ChromaDB con aislamiento por espacio (#17).
- retriever.py: búsqueda MMR con presupuesto de evidencia (issue #18).
"""
