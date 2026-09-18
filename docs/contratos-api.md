# 🔌 Contratos API v1 — NuevaMente

> **Estado**: especificación propuesta v1; Issue 03 la valida y congela con ejemplos ejecutables. Cambios posteriores requieren revisión contract-change.
> **Fuente**: `decisiones_proyecto.md` §3.3, §7, §16, §17. La implementación viva es el OpenAPI en `/docs` (issue `Issue 59`).
> **Uso**: backend implementa contra este documento; frontend construye contra este documento. Ambos lo tratan como la interfaz compartida.

---

## Convenciones generales

- **Base**: `/api` (sin `/v1`; el versionado vive en `schema_version=1.0` de los paquetes).
- **Autenticación**: `Authorization: Bearer <token_de_sesion>` en todas las rutas salvo creación de workspace, recuperación y `/health`.
- **Autorización**: todo recurso se resuelve desde la sesión validada; un ID ajeno responde **404** (no 403).
- **Idempotencia**: `POST` de upload, generación, persist y eventos de progreso aceptan header `Idempotency-Key`. Duplicado en curso → misma respuesta; mismo key con otro cuerpo → **409**.
- **Idioma de UI**: Accept-Language es/en/pt (default es) controla mensajes; idioma_salida controla contenido.
- **Trazabilidad**: toda respuesta incluye cabecera X-Request-ID; los errores JSON también incluyen request_id. No modificar cuerpos binarios de exportación ni el artefacto canónico para añadirlo.

### Envolvente de error (única para toda la API)

```json
{
  "error": {
    "code": "DOCUMENT_TOO_LARGE",
    "message": "El archivo supera el límite de 20 MB para PDF.",
    "details": { "limite_mb": 20, "recibido_mb": 25.4 }
  },
  "request_id": "req_8f14e45f"
}
```

| HTTP | code (ejemplos estables) | Significado |
|---|---|---|
| 400 | `INVALID_REQUEST` | Petición mal formada |
| 401 | `SESSION_INVALID` | Token/código inválido o sesión vencida |
| 404 | `NOT_FOUND` | Recurso inexistente **o ajeno** |
| 409 | `IDEMPOTENCY_CONFLICT` / `INVALID_STATE` | Estado incompatible o key reutilizada con otro cuerpo |
| 413 | `DOCUMENT_TOO_LARGE` | Archivo o contenido sobredimensionado |
| 422 | `EXPORT_INCOMPATIBLE` / `VALIDATION_ERROR` | Parámetros o combinación inválida |
| 429 | `QUEUE_FULL` / `RATE_LIMITED` / `RECOVERY_LOCKED` | Límite de uso o cola completa |
| 500 | `INTERNAL` | Error interno sin detalles sensibles |
| 503 | `STORAGE_UNAVAILABLE` / `PROVIDER_UNAVAILABLE` | Dependencia no disponible |

Los `code` son estables y no se traducen; los `message` se localizan según idioma de UI.

### Enums (valores de máquina estables)

| Enum | Valores |
|---|---|
| `perfil_destinatario` | `principiante` · `junior_ssr` · `lider_tecnico` · `ejecutivo` |
| `formato_salida` | `tutorial` · `flashcards` · `quiz` · `resumen_ejecutivo` · `guion_clase` |
| `nicho_sector` | `fintech` · `salud` · `ecommerce` · `general` |
| `nivel_detalle` | `didactico` · `practico` · `tecnico_profundo` |
| `idioma_salida` | `es` · `en` · `pt` |
| `status` (trabajo) | `queued` · `running` · `completed` · `rejected_quality` · `failed` · `cancelled` |
| `status` (documento) | `processing` · `ready` · `failed` |

> Las etiquetas visibles (p. ej. «Principiante / Transición de Carrera») viven en los catálogos i18n, no en el contrato.

---

## Rutas

### Espacios y sesiones (issues `Issue 09`, `Issue 16`)

| Método y ruta | Descripción |
|---|---|
| `POST /api/workspaces` | Crea espacio anónimo. **Devuelve una sola vez** `workspace_id`, `recovery_code` (≥128 bits, agrupado) y `token`. Expiración a 30 días de inactividad. |
| `POST /api/sessions/recover` | Canjea `recovery_code` por `token` nuevo. Máx. 5 intentos fallidos/min por origen. |
| `DELETE /api/sessions/current` | Cierra y revoca la sesión actual. |
| `POST /api/workspaces/current/recovery-code` | Rota el código (revoca sesiones previas) y devuelve el código nuevo una sola vez, junto con un token nuevo. |
| `DELETE /api/workspaces/current` | Bloquea el acceso y agenda el borrado físico de recursos. |

### Documentos (issues `Issue 19`, `Issue 11`)

| Método y ruta | Descripción |
|---|---|
| `POST /api/documents/upload` | multipart/form-data (campo file y título opcional) o application/json (`documento_titulo` + `documento_contenido` → TXT). Valida firma real y límites (20 MB PDF · 5 MB MD/TXT · 100 págs · 100k tokens). Responde **202** `{document_id, status: "processing"}`. |
| `GET /api/documents` | Lista paginada de documentos del espacio. |
| `GET /api/documents/{id}` | Estado + metadatos + **cobertura** (páginas/secciones procesadas y omitidas). |
| `DELETE /api/documents/{id}` | Borra original, índice, derivados, chat y progreso vinculados. |
| `GET /api/documents/{id}/sources/{chunk_id}` | Fragmento/página original autorizado (base de «Ver la fuente»; imágenes para diagramas). |

### Generación (issues `Issue 31`, `Issue 29`, `Issue 32`, `Issue 33`)

#### `POST /api/generate`

```json
{
  "document_id": "doc_01HX…",
  "perfil_destinatario": "principiante",
  "formato_salida": "flashcards",
  "nicho_sector": "general",
  "nivel_detalle": "didactico",
  "idioma_salida": "es",
  "alcance": { "tipo": "documento_completo" }
}
```

Respuesta **202**:

```json
{
  "generation_id": "gen_01HX…",
  "status": "queued",
  "status_url": "/api/generations/gen_01HX…",
  "events_url": "/api/generations/gen_01HX…/events"
}
```

`alcance` alternativo: `{ "tipo": "seccion", "seccion_id": "sec_3" }`.

#### `GET /api/generations/{id}`

Estado del trabajo con generation_id, status, contenido, persistencia y error. contenido solo existe cuando completed; error identifica el diagnóstico terminal. Para quiz, vista=estudiante es la predeterminada y omite claves/justificaciones no respondidas; el canónico completo se descarga por exportación autorizada. Un `rejected_quality` se consulta con **HTTP 200** y diagnóstico, sin `contenido_adaptado`.

#### `GET /api/generations/{id}/events` (SSE)

`text/event-stream` con heartbeat. Formato de evento:

```text
id: 42
event: progress
data: {"generation_id":"gen_01HX…","step":"critic","status":"running","iteration":2}
```

Soporta `Last-Event-ID` mientras exista registro; cerrar el stream **no** cancela el trabajo. Estados terminales: `completed` (solo tras persistencia confirmada) · `rejected_quality` · `failed` · `cancelled`.

#### `POST /api/generations/{id}/cancel` · `POST /api/generations/{id}/persist`

Cancelación explícita; reintento de **solo persistencia** de un aprobado retenido (≤24 h, mismo `objeto_id`, sin llamadas LLM).

#### `GET /api/generations`

Listado paginado con filtros: `documento`, `perfil`, `formato`, `idioma`, `desde`, `hasta`.

### Quiz y progreso (issues `Issue 35`, `Issue 36`, `Issue 41`)

| Método y ruta | Descripción |
|---|---|
| `POST /api/quizzes/{generation_id}/answers` | Body `{question_id, option_id, event_id}` → `{correcta, correct_option_id, justificacion, referencias}`. Determinista, sin LLM. La vista inicial del estudiante nunca recibe la clave. |
| `POST /api/progress/events` | Eventos idempotentes (`event_id` estable): `concepto_revisado`, `flashcard_vista`, el evento respuesta_quiz es interno y lo crea el endpoint de respuestas; primer_intento y acierto los calcula exclusivamente el backend. |
| `GET /api/progress` | Agregados del espacio: conceptos, flashcards, aciertos de primer intento, tiempo restante estimado. |

### Trabajos comunes (Issue 20)

GET /api/jobs/{id}, GET /api/jobs/{id}/events y POST /api/jobs/{id}/cancel permiten consultar, seguir y cancelar ingestión/chat/glosario.
Todos exigen sesión y ownership. Son vistas del mismo gestor, no otra cola.
Los POST asíncronos devuelven 202 con job_id, status_url, events_url y cancel_url.
El resultado de consulta contiene status, result (solo aprobado y disponible), error y posición si está en cola.
SSE usa job_id para trabajos comunes y generation_id para generaciones; id monotónico y heartbeat con línea vacía al cerrar cada evento.
Cerrar la conexión no cancela. Cancelar, fallar o rechazar nunca entrega borrador.
Chat/glosario usan Idempotency-Key por intención; caché aprobada puede responder 200.
Upload conserva document_id y agrega las URLs del trabajo de ingestión.

### Chat y glosario (issues `Issue 37`, `Issue 39`, `Issue 38`, `Issue 40`)

| Método y ruta | Descripción |
|---|---|
| `POST /api/chat` | `{document_id, pregunta, perfil_destinatario, idioma_salida}` → 202 y URLs del trabajo; su result contiene respuesta revisada con citas o abstención explícita. |
| `POST /api/glossaries` | `{document_id, perfil_destinatario, idioma_salida}` → 202 con glossary_id y URLs del trabajo, o 200 con resultado de caché verificado. |
| `GET /api/glossaries/{id}` | Glosario generado con definiciones citadas. |

### Exportaciones (issues `Issue 43`–`Issue 45`, `Issue 46`)

`GET /api/exports/{generation_id}?format=json|md|pdf|csv|tsv|apkg`

- Solo sobre trabajos `completed` (si no → **409**).
- `csv|tsv|apkg` solo para `formato_salida=flashcards` (si no → **422** `EXPORT_INCOMPATIBLE`).
- Descarga pasa por la API con verificación de ownership; sin secretos en el contenido exportado.

### Salud

`GET /api/health` → `{status, version}`. Sin secretos ni llamadas LLM.

---

## Paquete de salida: `PedagogicalOutput` (schema_version 1.0)

Estructura canónica del campo `contenido` cuando `status=completed` (decisión §16.3; la unión `contenido_adaptado` discrimina por `tipo`):

```json
{
  "schema_version": "1.0",
  "generation_id": "gen_01HX…",
  "status": "aprobado",
  "metadatos": {
    "perfil_aplicado": "principiante",
    "formato_generado": "flashcards",
    "nicho_sector": "general",
    "nivel_detalle": "didactico",
    "idioma_origen": "es",
    "idioma_salida": "es",
    "conceptos_clave": ["VCN", "Subredes", "Internet Gateway", "Security Lists"],
    "prerrequisitos": [],
    "objetivos_aprendizaje": ["Explicar qué es una VCN y para qué sirve"],
    "tiempo_estimado_estudio_minutos": 5,
    "alcance": { "tipo": "documento_completo", "secciones_cubiertas": ["Conceptos"] }
  },
  "documento_fuente": {
    "document_id": "doc_01HX…",
    "titulo": "Introducción a la Arquitectura de Redes VCN en OCI",
    "hash": "sha256:…",
    "version": "v1"
  },
  "contenido_adaptado": {
    "tipo": "flashcards",
    "titulo": "Dominando Redes en la Nube (VCN) desde Cero",
    "introduccion_contextualizada": "…",
    "items": [
      {
        "id": "fc_001",
        "frente": "¿Qué es una VCN en Oracle Cloud?",
        "dorso": "…",
        "pista_didactica": "…",
        "etiquetas": ["redes"],
        "referencias": [{ "chunk_id": "chk_…", "pagina": 2 }]
      }
    ]
  },
  "evaluacion_calidad": {
    "anclaje_fuente_score": 1.0,
    "cantidad_afirmaciones": 12,
    "cantidad_respaldadas": 12,
    "estado_evaluacion": "aprobada",
    "claridad_pedagogica": "alta",
    "adecuacion_perfil": "alta",
    "cobertura_objetivos": "completa",
    "coherencia_didactica": "alta",
    "verificacion_visual": "no_aplica",
    "observaciones": "…"
  },
  "referencias": [ { "chunk_id": "chk_…", "pagina": 2, "seccion": "Conceptos" } ],
  "created_at": "2026-10-01T12:00:00Z",
  "trazabilidad": {
    "modelo_generacion": "gemini-2.5-flash",
    "modelo_verificacion": "gemini-2.5-flash",
    "modelo_embeddings": "gemini-embedding-2",
    "prompt_version": "1.0",
    "parser_version": "1.0",
    "retrieval": { "k": 5, "fetch_k": 15, "lambda_mult": 0.7 }
  },
  "almacenamiento_oci": {
    "bucket": "nuevamente-contenidos-educativos",
    "objeto_id": "outputs/{ws}/gen_…/content.json"
  }
}
```

Notas de contrato:

- El score **solo** vive en `evaluacion_calidad.anclaje_fuente_score` (no en la raíz).
- `contenido_adaptado` es unión discriminada por `tipo` con los 5 modelos pedagógicos (`flashcards`, `quiz`, `tutorial`, `resumen_ejecutivo`, `guion_clase`); cada uno con sus validaciones (IDs únicos, `correct_option_id ∈ options`, listas no vacías, duraciones positivas).
- En quiz, la vista de estudiante omite `correct_option_id` y `justificacion` hasta responder; el paquete exportado sí las incluye (sección de soluciones separada en PDF).
- El canónico solo contiene bucket/objeto_id; no confirma su propia escritura. persistencia.status_upload se añade exclusivamente en la respuesta del trabajo tras verificar el objeto.
- alcance conserva la estructura de entrada y agrega secciones cubiertas; no alterna objeto/string según formato.
- La respuesta HTTP del trabajo agrega `persistencia.status_upload` en el nivel del trabajo cuando `completed`.

---

### Recuperación, rotación y borrado

La UI permite copiar/descargar el código una vez, rotarlo, cerrar sesión y borrar el espacio con consecuencias visibles (Issue 16). Borrar revoca acceso inmediatamente; la respuesta confirma recepción y el backend mantiene el diagnóstico de limpieza sin reabrir una sesión revocada. La prueba de borrado usa observabilidad administrativa, no acceso del usuario borrado.

## Reglas transversales que el contrato asume

1. Errores de trabajo ≠ errores HTTP: consultar un trabajo `rejected_quality` es 200 + estado; exportar algo no aprobado es 409.
2. `completed` implica persistencia confirmada. Si falla, el estado es failed/STORAGE_UNAVAILABLE con persist disponible mientras dure la retención, nunca completed. En desarrollo, persistencia indica provider=mock y no afirma completado en OCI.
3. Límites operativos (§7.5) se expresan vía 429 con `code` específico y mensaje con posición/expectativa.
4. Todo listado es paginado (`limit`, `cursor`).
5. Los nombres internos de clases pueden diferir; este JSON es el contrato público y sus claves no cambian sin `contract-change`.
