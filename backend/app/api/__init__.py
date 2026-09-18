"""Capa HTTP del backend (carril API): rutas FastAPI y dependencias compartidas.

Territorio y contrato según docs/guia-trabajo-equipo.md §2: peticiones y
respuestas de docs/contratos-api.md, con schemas Pydantic de app/schemas/.

Archivos previstos:
- deps.py: dependencias inyectables compartidas (issue #07).
- routes/: un módulo por recurso (ver routes/__init__.py).

Reglas relevantes: errores estándar con `error.code/message/details` y
`request_id` (§7.3, issue #07); cada recurso se autoriza por espacio, incluido
SSE (§11.2).
"""
