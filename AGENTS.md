# AGENTS.md — Master Orchestrator & Skills Governance

> **Proyecto**: 🎓 NuevaMente — Sistema Inteligente de Adaptación y Generación de Contenido Educativo  
> **Equipo**: Grupo 10 / Equipo 38 (Oracle ONE — Oracle Next Education)  
> **Área Técnica**: RAG Avanzado, Multi-Agente con LangGraph, OCI Always Free ($0.00) y Frontend Craft  
> **Estado de Gobernanza**: Activo y Vinculante para todos los Agentes de IA y Desarrolladores

---

## 1. Visión y Propósito Maestro

**NuevaMente** es una plataforma inteligente capaz de ingerir documentación técnica densa (manuales de software, especificaciones de arquitectura, artículos, APIs) y transformarla automáticamente en contenidos pedagógicos estructurados y personalizados según el perfil cognitivo del destinatario, el nicho industrial y el formato educativo seleccionado.

### Pilares Innegociables del Proyecto
1. **Fidelidad Fáctica Absoluta**: Cero alucinaciones. Medido y garantizado mediante el algoritmo de fidelidad de Ragas (`anclaje_fuente_score` de `0.0` a `1.0`, con umbral mínimo de aprobación de `0.85`).
2. **Orquestación Multi-Agente con LangGraph**: Grafo de decisión con roles especializados: *Supervisor/Router*, *Researcher*, *Writer* y *Critic* con retroalimentación adaptativa.
3. **Infraestructura Oracle Cloud (OCI) Always Free Estricta**: Persistencia en OCI Object Storage (`nuevamente-contenidos-educativos`) con política estricta de costo `$0.00` garantizado y fallback mock automático para desarrollo local/offline.
4. **Frontend Sobresaliente (Linear Design System)**: Interfaz oscura, densa, elegante y de alta precisión en Streamlit/Gradio (`#010102`, `#5e6ad2`) con evaluación interactiva de quizzes en tiempo real y exportación Anki (`.apkg`/CSV).
5. **Calidad de Ingeniería Rigurosa**: Especificación previa (`spec-driven-development`), modelo de dominio ubicuo (`domain-modeling`), diagramas con balizas de evidencia (`archify-system-design`) y commits atómicos (`git-workflow-and-versioning`).

---

## 2. Mapa Maestro de Skills Registradas

Todas las skills del proyecto residen en `.agents/skills/<skill-name>/SKILL.md` y están indexadas formalmente en `.agents/skills.json`.

| # | Skill | Categoría | Archivo Maestro | Responsabilidad Principal |
|---|---|---|---|---|
| **01** | [`spec-driven-development`](.agents/skills/spec-driven-development/SKILL.md) | Metodología | [SKILL.md](.agents/skills/spec-driven-development/SKILL.md) | Especificación estructurada previa al código; desglose de capacidades y criterios de aceptación. |
| **02** | [`domain-modeling`](.agents/skills/domain-modeling/SKILL.md) | Dominio | [SKILL.md](.agents/skills/domain-modeling/SKILL.md) | Mantenimiento del lenguaje ubicuo en `CONTEXT.md` y registro de decisiones en `docs/adr/`. |
| **03** | [`grill-with-docs`](.agents/skills/grill-with-docs/SKILL.md) | Dominio | [SKILL.md](.agents/skills/grill-with-docs/SKILL.md) | Entrevista socrática implacable en árbol de diseño y frontera de preguntas para estresar requerimientos. |
| **04** | [`archify-system-design`](.agents/skills/archify-system-design/SKILL.md) | Arquitectura | [SKILL.md](.agents/skills/archify-system-design/SKILL.md) | Visualización arquitectónica C4/Mermaid y mapeo de balizas de evidencia enlazadas a código. |
| **05** | [`api-and-interface-design`](.agents/skills/api-and-interface-design/SKILL.md) | Calidad | [SKILL.md](.agents/skills/api-and-interface-design/SKILL.md) | Diseño de contratos tipados con Pydantic, boundaries limpios y schemas de comunicación. |
| **06** | [`rag-and-grounding`](.agents/skills/rag-and-grounding/SKILL.md) | Dominio NuevaMente | [SKILL.md](.agents/skills/rag-and-grounding/SKILL.md) | Ingestión (PDF/MD), chunking, ChromaDB y cálculo de fidelidad Ragas (`anclaje_fuente_score`). |
| **07** | [`pedagogical-orchestrator`](.agents/skills/pedagogical-orchestrator/SKILL.md) | Dominio NuevaMente | [SKILL.md](.agents/skills/pedagogical-orchestrator/SKILL.md) | Grafo multi-agente LangGraph, matrices pedagógicas (4 perfiles, 5 formatos) y exportador Anki. |
| **08** | [`oci-always-free-storage`](.agents/skills/oci-always-free-storage/SKILL.md) | Dominio NuevaMente | [SKILL.md](.agents/skills/oci-always-free-storage/SKILL.md) | Conexión OCI Object Storage SDK, reglas Always Free $0.00 y proveedor Mock local transparente. |
| **09** | [`linear-design-system`](.agents/skills/linear-design-system/SKILL.md) | Frontend UI | [SKILL.md](.agents/skills/linear-design-system/SKILL.md) | Tokens visuales Linear (dark #010102, lavender #5e6ad2), cards, badges, quizzes interactivos y medidor. |
| **10** | [`frontend-ui-engineering`](.agents/skills/frontend-ui-engineering/SKILL.md) | Frontend UI | [SKILL.md](.agents/skills/frontend-ui-engineering/SKILL.md) | Composición limpia de componentes, accesibilidad WCAG, microinteracciones y ergonomía UX. |
| **11** | [`security-and-hardening`](.agents/skills/security-and-hardening/SKILL.md) | Calidad | [SKILL.md](.agents/skills/security-and-hardening/SKILL.md) | Sanitización de inputs, mitigación de prompt injection, higiene de credenciales OCI y LLM. |
| **12** | [`debugging-and-error-recovery`](.agents/skills/debugging-and-error-recovery/SKILL.md) | Calidad | [SKILL.md](.agents/skills/debugging-and-error-recovery/SKILL.md) | Protocolo Stop-the-line, aislamiento de fallos, diagnóstico reproducible sin parches ciegos. |
| **13** | [`git-workflow-and-versioning`](.agents/skills/git-workflow-and-versioning/SKILL.md) | Calidad | [SKILL.md](.agents/skills/git-workflow-and-versioning/SKILL.md) | Commits atómicos, conventional commits, trunk-based delivery y etiquetado de versiones. |

---

## 3. Protocolo de Armonía y Reglas Anti-Contradicción

Para garantizar que múltiples agentes de IA (o desarrolladores humanos) trabajen de forma sinérgica sin pisar convenciones ni generar incoherencias, se declaran las siguientes **reglas de supremacía**:

### Regla 1: Soberanía de Fidelidad sobre Fluidez Ficticia
- **`rag-and-grounding` prevalece sobre `pedagogical-orchestrator`**: Si el agente *Writer* produce un texto con excelente elocuencia pero el cálculo de `anclaje_fuente_score` arroja un valor inferior a `0.85`, el agente *Critic* tiene la obligación de rechazar o forzar la revisión del contenido. La elocuencia jamás excusa la alucinación.

### Regla 2: Pydantic como Única Fuente de Verdad para Contratos de Datos
- **`api-and-interface-design` y `pedagogical-orchestrator` gobiernan los payloads**: Todo intercambio de información estructurada entre agentes, backend, storage y UI debe validarse contra los esquemas oficiales Pydantic (`PedagogicalOutput`, `FlashcardDeck`, `InteractiveQuiz`, `PracticalTutorial`, `ExecutiveSummary`). Queda terminantemente prohibido generar JSON no tipado o campos no consensuados.

### Regla 3: Precedencia de Costo Cero y Fallback Mock Transparente
- **`oci-always-free-storage` garantiza costo $0.00 absoluto**: La aplicación jamás debe fallar ni bloquear al usuario si las credenciales de Oracle Cloud no están presentes. En ausencia de `~/.oci/config` o variables de entorno OCI, el sistema activa automáticamente `LocalMockStorageProvider` en `.data/oci_mock_storage/` con la misma interfaz pública. La capa cloud es Always Free y no requiere tarjeta con consumo para operar el demo.

### Regla 4: Cohesión de Frontend: Tokens Linear + Ergonomía UI
- **`linear-design-system` define el aspecto visual, `frontend-ui-engineering` define la estructura**:
  - Los colores `#010102` (canvas), `#0f1011` (tarjetas), `#5e6ad2` (lavender) y bordes `#23252a` son obligatorios.
  - La composición de componentes, accesibilidad para teclado, estados de carga y manejo de sesión se rigen por `frontend-ui-engineering`.
  - Queda prohibido usar colores por defecto de Streamlit o gradientes estridentes no contemplados en el design system.

### Regla 5: Disciplina de Especificación Previa
- **No programar sin contexto ni especificación**: Ningún cambio sustancial de arquitectura o pipeline se implementa sin haber pasado por `spec-driven-development` y haber validado los términos con `domain-modeling` en `CONTEXT.md`.

---

## 4. Flujo de Trabajo Secuencial para Agentes

Cualquier agente que participe en la construcción de NuevaMente debe seguir esta secuencia ordenada por fases:

```
[FASE 1: DESCUBRIMIENTO & MODELADO]
   ├── spec-driven-development: Capacidad y criterios de aceptación
   ├── grill-with-docs: Cuestionamiento socrático de supuestos
   └── domain-modeling: Definición de términos en CONTEXT.md y ADRs
              │
              ▼
[FASE 2: ARQUITECTURA & CONTRATOS]
   ├── archify-system-design: Diagrama de flujo C4/Mermaid con balizas de evidencia
   └── api-and-interface-design: Modelos Pydantic y endpoints
              │
              ▼
[FASE 3: CORE TÉCNICO NUEVAMENTE]
   ├── rag-and-grounding: Ingestión (PDF/MD), chunking, ChromaDB y métrica Ragas
   ├── pedagogical-orchestrator: Grafo LangGraph (Supervisor, Researcher, Writer, Critic)
   └── oci-always-free-storage: Conector OCI SDK + Fallback Mock local
              │
              ▼
[FASE 4: EXPERIENCIA DE USUARIO (UI/FRONTEND)]
   ├── linear-design-system: Tokens oscuros, cards, gauges de fidelidad, quizzes
   └── frontend-ui-engineering: Streamlit/Gradio interactivo y feedback en vivo
              │
              ▼
[FASE 5: VERIFICACIÓN, HARDENING & RELEASE]
   ├── security-and-hardening: Sanitización, prompt injection, secretos
   ├── debugging-and-error-recovery: Triage Stop-the-Line ante fallos
   └── git-workflow-and-versioning: Commits atómicos convencionales y README
```

---

## 5. Especificaciones de Dominio de NuevaMente

### Parámetros de Operación

#### 1. Perfiles de Destinatario (4)
1. **`Principiante / Transición de Carrera`**: Enfoque didáctico y empático, analogías del mundo real, explicaciones conceptuales paso a paso, sin asumir conocimientos previos.
2. **`Desarrollador Junior / Semi Senior`**: Enfoque práctico orientado a código, snippets funcionales, pasos de configuración, advertencias sobre errores comunes y mejores prácticas.
3. **`Líder Técnico / Arquitecto`**: Enfoque de alto nivel, patrones de diseño, escalabilidad, decisiones de trade-off, no funcionales y resiliencia.
4. **`Gestor / Ejecutivo (No Técnico)`**: Resúmenes orientados a valor, impacto en negocio, ROI, métricas clave, riesgos y cronogramas.

#### 2. Formatos Pedagógicos de Salida (5)
1. **`Guía Práctica Paso a Paso (Tutorial)`**: Flujo secuencial con prerrequisitos, comandos, salidas esperadas y pasos de validación.
2. **`Flashcards de Memorización`**: Pares pregunta/respuesta concisos y atómicos, exportables a Anki (`.apkg` y TSV/CSV).
3. **`Quiz Interactivo con Justificaciones`**: Preguntas de opción múltiple (4 opciones) con evaluación inmediata en la UI y desglose didáctico de por qué la correcta es válida y por qué los distractores fallan.
4. **`Resumen Ejecutivo (TL;DR)`**: Puntos clave en viñetas, contexto estratégico y conclusiones ejecutivas.
5. **`Guion de Clase / Video`**: Estructura temporal con narración para profesor/instructor, notas de diapositivas y preguntas interactivas para los estudiantes.

#### 3. Nichos de Aplicación (4)
- `Fintech`: Terminología de transacciones, seguridad de pagos, regulaciones y baja latencia.
- `Salud`: Confidencialidad de datos, trazabilidad clínica y precisión crítica.
- `E-commerce`: Catálogos, experiencia de usuario, conversiones y alta disponibilidad.
- `General`: Conceptos de ingeniería y computación agnósticos de industria.

---

## 6. Demostración Práctica Obligatoria (3 Escenarios Mínimos)

Para garantizar el cumplimiento de los requerimientos de evaluación del Hackathon, el sistema debe incluir y demostrar con éxito al menos 3 escenarios reales:

1. **Escenario A (Técnico / Fintech)**:
   - *Documento*: Manual técnico de integración de Webhooks y APIs de pagos.
   - *Perfil*: Desarrollador Junior / Semi Senior.
   - *Formato*: Guía Práctica Paso a Paso (Tutorial) con snippets de código.
   - *Validación*: `anclaje_fuente_score >= 0.88`, subida a OCI Object Storage y visualización en UI.

2. **Escenario B (Memorización / E-commerce)**:
   - *Documento*: Especificación de arquitectura de microservicios para checkout.
   - *Perfil*: Principiante / Transición de Carrera.
   - *Formato*: Flashcards de Memorización con exportación a mazo Anki (.apkg/CSV).
   - *Validación*: Generación de archivo Anki funcional y visualización con flip interactivo.

3. **Escenario C (Ejecutivo / Salud)**:
   - *Documento*: Guía de gobernanza de datos y privacidad en registros médicos electrónicos.
   - *Perfil*: Gestor / Ejecutivo (No Técnico).
   - *Formato*: Resumen Ejecutivo (TL;DR) con Quiz de validación de comprensión gerencial.
   - *Validación*: Evaluación del quiz en tiempo real en la UI con scorecard de fidelidad.

---

## 7. Instrucciones para Agentes de IA

Cuando un agente autónomo o asistente de código sea activado en este repositorio:
1. **Consulte este archivo (`AGENTS.md`)** para entender la fase del proyecto y la skill correspondiente.
2. **Lea el archivo `SKILL.md` específico** en `.agents/skills/<skill-name>/SKILL.md` antes de escribir código en ese dominio.
3. **Respete el modelo de datos**: Utilice siempre tipado estricto con Pydantic.
4. **Verifique la métrica de anclaje**: Invoque siempre la rutina de verificación Ragas para documentar el `anclaje_fuente_score`.
5. **Documente las decisiones**: Si realiza una elección técnica con impacto irreversible, cree el ADR respectivo en `docs/adr/`.
