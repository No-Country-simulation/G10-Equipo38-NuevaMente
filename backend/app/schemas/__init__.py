"""Contratos compartidos v1 (carril QAD, revisión API + UI + AGT + RAG).

CONGELADO como v1 por el issue #03: este paquete es el habilitador de
paralelismo del proyecto — todos los carriles construyen contra estos
modelos en vez de acordar formas de JSON por chat. Cualquier cambio
posterior (campo nuevo, valor de enum, código de error) exige un PR
etiquetado `contract-change` con revisión de los cuatro carriles y
actualización de docs/contratos-api.md en el mismo PR (guía §2).

Mapa del paquete (fuentes: §7, §16, §3.3 y docs/contratos-api.md):

- enums.py: valores de máquina estables (perfiles, formatos, nichos,
  detalle, idiomas, estados de trabajo y documento). Las etiquetas
  traducidas viven en frontend/i18n (issue #06), NUNCA aquí.
- pedagogical.py: los 5 formatos de contenido_adaptado como unión
  discriminada por `tipo`, más Referencia, Alcance y la vista de estudiante
  del quiz (sin claves ni justificaciones).
- requests.py: bodies de entrada (GenerateRequest, UploadRequest,
  ChatRequest, GlossaryRequest, QuizAnswerRequest, ProgressEvent).
- responses.py: PedagogicalOutput (paquete canónico schema_version 1.0),
  evaluacion_calidad, trazabilidad, persistencia y la respuesta del trabajo
  de generación.
- errors.py: envoltorio único de error (code/message/details + request_id)
  y la tabla de códigos estables de §7.3.
- sse.py: eventos de progreso para generaciones (generation_id) y trabajos
  comunes (job_id): id monotónico, step, status, iteration.
- internal.py: contratos INTERNOS entre módulos del backend (documento
  parseado, chunk, evidencia recuperada, verificación visual, presupuesto
  de llamadas, trabajos comunes). No viajan por la API pública: no están
  congelados.

Regla de estilo del contrato: las CLAVES del JSON público van en español
(son el contrato); los nombres de clase pueden ir en inglés (§16.3). Todos
los modelos públicos usan extra="forbid": rechazar campos desconocidos es
lo que mantiene el contrato congelado de verdad (§7.2).

Tests de vigilia: backend/tests/test_schemas.py hace round-trip de los 5
formatos y rechaza ejemplos inválidos; si un cambio rompe el contrato, la
CI lo bloquea antes del merge.
"""
