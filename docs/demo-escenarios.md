# 🎬 Estrategia de Demo y Escenarios — NuevaMente

> **Objetivo**: demostrar el 100% de requisitos mínimos y diferenciales del enunciado con evidencia verificable, incluyendo una ejecución **en vivo real** con persistencia OCI.
> **Fuente**: `decisiones_proyecto.md` §18 · **Issues**: `Issue 05`, `Issue 55`, `Issue 56`, `Issue 54`.
> **Principio**: los ejemplos precargados nunca se presentan como generación en vivo, y la grabación de respaldo no sustituye la integración activa.

---

## 1. Fuente común y tres escenarios oficiales

Los tres escenarios usan el **mismo** documento (`documents/redes_vcn_oci.pdf`, mismo `document_id` y hash de versión), lo que demuestra ≥2 perfiles y ≥2 formatos sobre idéntica fuente (requisito 4):

| Escenario | Perfil | Formato | Nicho | Idioma | Qué demuestra |
|---|---|---|---|---|---|
| **A** | Principiante / Transición | Flashcards | General | Español | Analogías y lenguaje accesible sin tecnicismos |
| **B** | Desarrollador Junior / Semi | Tutorial | E-commerce | Inglés | Pasos prácticos, código, multilingüe EN |
| **C** | Gestor / Ejecutivo | Resumen Ejecutivo | Fintech | Portugués | Síntesis de alto nivel, multilingüe pt-BR |

Documentos complementarios de la biblioteca demo (`Issue 05`): `integracion_apis_pagos.md` (Fintech) y `gobernanza_datos_salud.md` (Salud, con ejemplos ficticios). No sustituyen la comparación sobre la fuente común.

---

## 2. Guion de demostración (10–12 min)

| # | Min | Momento | Qué se muestra | Requisito/diferencial |
|---|---|---|---|---|
| 1 | 0:00 | Apertura | Problema (semanas → minutos) y recorrido de 30 s de la UI | — |
| 2 | 0:30 | Acceso anónimo | Crear espacio y guardar el código en privado; ocultarlo en proyección y grabación | Extra: acceso anónimo |
| 3 | 1:00 | **Carga en vivo real** | Subir el PDF VCN; estado processing → ready con cobertura | R1 + R6 (original en OCI) |
| 4 | 1:45 | **Generación en vivo real** | Escenario A con progreso SSE visible (etapas e intentos) | R2, R3, R5 + SSE |
| 5 | 3:30 | Resultado A | Flashcards interactivas + panel de fuentes (ver la página original) + panel de calidad | R5, fidelidad |
| 6 | 4:30 | Escenarios B y C | Trío A/B/C precargado con un mismo document_id/hash, etiquetado; comparación lado a lado | R4 (2 perfiles/2 formatos) + i18n |
| 7 | 6:00 | Quiz en tiempo real | Responder una bien y una mal → feedback inmediato con justificación | Diferencial: quiz |
| 8 | 7:00 | Multimodal | Pregunta derivada del diagrama → vista de la página original rasterizada | Diferencial: multimodal |
| 9 | 8:00 | Grafo multi-agente | Explicación con diagrama: Researcher→Writer→Critic, 3 intentos, umbrales | Diferencial: LangGraph |
| 10 | 8:45 | **Rechazo intencional** | Caso real de rechazo previamente verificado, mostrando fuente y diagnóstico sin borrador | Anti-alucinación |
| 11 | 9:30 | Exportaciones | Descarga PDF didáctico + CSV/TSV/APKG importado en Anki Desktop | Diferencial: exportación |
| 12 | 10:30 | Recuperación + URL pública | Cerrar sesión → recuperar con código → historial/progreso intactos; app corriendo en OCI Compute | Diferencial: OCI Compute + extra recuperación |
| 13 | 11:15 | Cierre | Arquitectura en 30 s + tiempos medidos reales | R8 |

La carga nueva puede producir otro document_id: se muestra como ejecución adicional A′. La evidencia de los tres escenarios usa el trío precargado A/B/C del mismo ID/hash; no se compara A′ como si tuviera su ID.

Los tiempos del guion son objetivos de ensayo, no garantías. La generación puede consumir hasta 300 s más espera: ajustar duración al tiempo asignado por la organización y a las mediciones.

**Regla del paso 3–4**: la prueba funcional exige una carga/generación nueva con OCI real. La presentación procura mostrarla en vivo; una falla se declara y se usa respaldo, sin simular éxito ni sustituir la evidencia de integración activa (§18.3).

---

## 3. Checklist de evidencia por diferencial

Cada ítem se documenta con enlace/captura/objeto OCI (responsable: `Issue 56`):

- [ ] **OCI Compute**: URL pública con HTTPS operativa + captura de consola de la VM A1 en capa Always Free.
- [ ] **Multi-agente LangGraph**: traza de una generación con los 4 roles y conteo de intentos (registro de eventos).
- [ ] **Quiz en tiempo real**: video/captura de respuesta correcta e incorrecta con feedback y justificación.
- [ ] **Multimodal**: descripción del diagrama VCN + cita a la página original + verificación visual aprobada.
- [ ] **Exportación multiformato**: JSON, MD, PDF abiertos + importación CSV/TSV y APKG en Anki Desktop (captura).
- [ ] **OCI Object Storage (obligatorio)**: objetos en `source_documents/` y `outputs/` visibles en consola + JSON recuperado desde OCI (`Issue 54`).
- [ ] **3 escenarios**: A/B/C con mismo `document_id` y hash (listado de generaciones).
- [ ] **Extras**: chat RAG con cita, glosario, progreso recuperado, comparación, caso de rechazo.

---

## 4. Medición y honestidad en cifras

- Se muestran **tiempos medidos** de las corridas reales (`Issue 55` los registra), no promesas de instantaneidad.
- El `anclaje_fuente_score` se presenta como «consistencia con la fuente verificada», nunca como probabilidad de verdad (§19.4).
- Las limitaciones conocidas (cuota compartida, retención 30 días, material público) se declaran si se preguntan.

---

## 5. Plan de contingencia

| Falla durante la demo | Respuesta |
|---|---|
| Cuota Gemini agotada | Resultados demo precargados etiquetados + grabación de respaldo + explicación del presupuesto de llamadas |
| VM OCI caída | Commit/tag exacto validado de la entrega + Docker local con `MOCK_OCI=0` y credenciales propias (fallback técnico honesto, declarado como entorno local) |
| Generación en vivo tarda >3 min | Mientras corre: narrar el diagrama del grafo (paso 9 adelantado); la cola avisa posición y estado |
| El caso de rechazo produce otra respuesta | Revisar cobertura y respaldo: una respuesta honesta diferente no es automáticamente un bug. Mostrar el rechazo real registrado; nunca forzar aprobación ni fingir un rechazo |

**Respaldo obligatorio** (`Issue 56`): grabación completa de un ensayo exitoso + JSON de los 3 escenarios + capturas de consola OCI, disponibles offline en el dispositivo del presentador.

---

## 6. Precondiciones del día de la demo

- [ ] Cuotas reales de generación, juez, embeddings y visión verificadas al ensayar y al presentar; reservar por llamadas/tokens, no por una equivalencia fija de generaciones.
- [ ] Presupuesto OCI (solicitudes/almacenaje) con holgura ≥50% (contador de `Issue 50`).
- [ ] Espacios demo intactos y re-generables (runbook de `Issue 49` probado).
- [ ] Grabación de respaldo descargada localmente.
- [ ] Código de recuperación del espacio demo guardado de forma segura por el presentador.
- [ ] Ensayo cronometrado completo ≤12 h antes.
