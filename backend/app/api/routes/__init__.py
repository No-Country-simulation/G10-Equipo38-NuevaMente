"""Endpoints por recurso del API (§15). Un módulo por recurso:

- documents.py: carga y gestión de documentos (issue #19).
- generate.py: trabajos de generación y progreso por SSE (issue #31).
- chat.py: chat RAG sobre el documento activo (issue #37).
- exports.py: exportación JSON/MD/PDF/CSV/TSV/APKG (issue #43).
- workspaces.py: creación, recuperación y borrado de espacios anónimos (#9).
- quizzes.py: respuestas y feedback inmediato de quiz (issue #35).
- progress.py: progreso de estudio (issue #41).
- glossary.py: glosario adaptado (issue #39).

Los contratos de petición/respuesta viven en app/schemas/ y se documentan en
docs/contratos-api.md; cambiarlos exige PR etiquetado `contract-change`.
"""
