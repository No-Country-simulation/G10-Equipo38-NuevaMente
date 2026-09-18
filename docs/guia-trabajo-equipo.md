# 👥 Guía de Trabajo del Equipo — NuevaMente

> Cómo trabajamos los 6 carriles en paralelo sin pisarnos: ramas, commits, PRs, tablero y ceremonias.
> **Complementos**: [plan de implementación](plan-implementacion.md) (issues y fases) · [contratos API](contratos-api.md) · `decisiones_proyecto.md` §13 (gestión de proyecto).

---

## 1. Ramas y flujo Git

Estrategia **Git Flow simplificado** (decisión §13.2):

```text
main ─────────────────────────────────────────────▶ producción (desplegada en OCI)
  │                                              ▲
  └── develop ───────────────────────────────────┘ integración continua
        │        ▲        ▲        ▲
        │        │        │        │
        ├─ feature/api-generacion-sse
        ├─ feature/rag-chunker
        ├─ feature/ui-quiz-interactivo
        └─ feature/oci-provider
```

Reglas:

1. **`main`** es la rama de entrega; protegida: solo se llega por PR desde `develop`, salvo hotfix validado desde main y sincronizado inmediatamente con develop.
2. **`develop`** es la integración; protegida: PR con revisión aprobada y CI verde.
3. Ramas de feature: `feature/issue-<nn>-<slug>` (p. ej. `feature/issue-12-chunker`). **Cortas**: se abren al tomar el issue y se mergean apenas cumple sus criterios; sincronizar con `develop` a diario.
4. Hotfix en producción: rama `hotfix/*` desde `main`, merge a `main` **y** a `develop`.
5. Prohibido push directo a `main`/`develop`; prohibido rebase de ramas compartidas.

### Commits — Conventional Commits

| Prefijo | Uso | Ejemplo |
|---|---|---|
| `feat:` | funcionalidad | `feat(rag): chunker estructural con chunk_id estable` |
| `fix:` | corrección | `fix(ui): flashcard no registraba revisión` |
| `docs:` | documentación | `docs: diagrama de contenedores en arquitectura.md` |
| `test:` | pruebas | `test(api): estados de vida del trabajo de generación` |
| `refactor:` | sin cambio funcional | `refactor(storage): extraer interfaz del proveedor` |
| `style:` | formato | `style: aplicar ruff` |
| `ci:` | CI/CD | `ci: cachear dependencias de backend` |
| `chore:` | mantenimiento | `chore: fijar versiones exactas de requirements` |

Convenciones: scope = módulo (`api`, `rag`, `agt`, `ui`, `inf`, `qad`) · imperativo · referencia el issue (`closes #12`) · un commit = una idea.

---

## 2. Reglas de límites entre módulos (por qué no nos pisamos)

| Módulo | Territorio | Contrato que comparte |
|---|---|---|
| **API** | `backend/app/api/`, `jobs/`, `session/` | Peticiones/respuestas de [`contratos-api.md`](contratos-api.md) |
| **RAG** | `backend/app/core/rag/` | Documento, chunk, evidencia, filtros de acceso |
| **AGT** | `backend/app/core/agents/`, `faithfulness/` | Estado del grafo, borrador tipado, evaluación |
| **UI** | `frontend/` | Contratos API + catálogos i18n |
| **INF** | `storage/`, `docker-compose.yml`, despliegue | `StorageProvider` + manifiestos |
| **QAD** | `.github/`, `tests/`, `documents/`, `docs/`, README | Fixtures y criterios de aceptación |

- Quien necesita tocar un archivo de otro carril (p. ej. AGT necesita un metadato nuevo del chunk de RAG): **abre issue/comenta en el issue dueño del contrato** y se resuelve en el contrato compartido, no editando el módulo ajeno por cuenta propia.
- Cambios de contrato (schemas, enums, códigos de error): PR etiquetado `contract-change`, revisión de API + UI + AGT + RAG, actualización de `docs/contratos-api.md` en el mismo PR.

---

## 3. Tablero y issues

- **Fuente de issues**: [`docs/issues/`](issues/) → se copian a GitHub con labels: `fase-N`, `carril-api|rag|agt|ui|inf|qad`, tamaño `S|M|L`.
- Columnas del tablero: `Backlog → In progress → In review → Done`.
- **Regla de dependencias**: un issue no pasa a `In progress` hasta que todos sus `Depende de` están en `Done` (mergeados en `develop`).
- Cada integrante tiene **≤2 issues** en `In progress` simultáneos.
- Bloqueo detectado: comentar el issue con el bloqueo + aviso en el canal; el carril QAD re-planifica la oleada si el bloqueo dura >1 día.
- Un issue vuelve a `Backlog` (no se fuerza) si sus criterios de aceptación resultaron mal definidos: se corrige el issue y se explica.

---

## 4. Definition of Done (recordatorio)

La lista completa está en el [plan maestro §9](plan-implementacion.md#9-definition-of-done-global-y-plantilla-de-issue). Resumen operativo del PR:

1. CI verde (ruff + pytest del módulo).
2. Criterios de aceptación del issue marcados en el PR (checklist copiada).
3. Revisión aprobada por un integrante de **otro** carril cuando el diff toca contratos o módulos compartidos.
4. `docs/` actualizado si cambia un contrato o un comportamiento documentado.
5. Sin secretos en el diff (el revisor verifica con la checklist del PR).

### Checklist de PR (plantilla en GitHub)

```markdown
- [ ] Issue: #<n> (`Issue <nn>` del plan)
- [ ] Criterios de aceptación del issue cumplidos
- [ ] Verificación adecuada al cambio, con tests para comportamiento
- [ ] ruff check / format limpios
- [ ] Contrato afectado: no / sí (PR etiquetado contract-change + docs actualizados)
- [ ] Sin secretos ni datos personales
- [ ] Screenshots si toca UI
```

---

## 5. Ceremonias y ritmo

| Ceremonia | Cadencia | Propósito |
|---|---|---|
| **Sync de 15 min** | diaria | qué avancé / qué bloqueo / qué necesito de otro carril |
| **Revisión de hitos** | al cierre de cada fase (H0–H6) | demo interna del hito; otros carriles avanzan según dependencias, sin barrera global |
| **Revisión de contratos** | bajo demanda | cambios `contract-change` (asincrónica, 24 h máx. de respuesta) |
| **Ensayo de demo** | al cerrar requisitos de Issue 56 | guion completo con cronómetro |

Frecuencia de integración: merge a `develop` idealmente diario por carril; ramas con >3 días de vida se sincronizan obligatoriamente.

---

## 6. Reglas de oro del hackathon (resumen)

1. **Nada a main sin CI y revisión; hotfix sigue la excepción controlada de §1.**
2. **Mock explícito siempre**: `MOCK_OCI=1` para desarrollo/CI; la entrega acredita lo real (`Issue 54`).
3. **No se cambia un contrato sin `contract-change`** — es lo que nos permite avanzar en paralelo.
4. **Las dependencias de issues no se saltan**: ahorrar 1 día saltándolas cuesta 3 en integración.
5. **Si un requisito del enunciado entra en riesgo, se escala ese día** (§8 del plan maestro exige reestimar o redistribuir; mínimos, diferenciales y extras acordados no se recortan automáticamente).
6. Cada merge deja `develop` desplegable: en cualquier momento podemos revisar localmente con mock; esto no acredita despliegue ni persistencia OCI real.
