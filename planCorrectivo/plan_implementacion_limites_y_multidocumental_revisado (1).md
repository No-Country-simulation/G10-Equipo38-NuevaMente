# Plan revisado — Límites configurables y síntesis multidocumental

Revisión: 22 de octubre de 2026.

Documento de trabajo. Corrige y sustituye, como propuesta técnica, el plan `plan_implementacion_limites_y_multidocumental.md` proporcionado por el usuario. No acredita implementación, aprobación del equipo ni merge. El archivo original se conserva.

Base verificada directamente en GitHub: `develop` en [`f1f9ae61a5e52c7f0ef2411754ebd94738400318`](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/tree/f1f9ae61a5e52c7f0ef2411754ebd94738400318). Antes de implementar hay que actualizar esta comparación, leer el issue acordado y verificar disponibilidad y dependencias. No se crean issues, ramas, commits ni PRs mediante esta revisión.

## 1. Objetivo y alcance

Implementar dos mejoras: límites de documentos y generaciones configurables mediante `.env`, y síntesis multidocumental optativa en generación, chat y glosario. Mantener el flujo de una sola fuente y los defaults actuales de cinco documentos y veinte generaciones.

La consulta sobre hashes se refiere exclusivamente a los JSON de evidencia para el README final. Su tratamiento queda en §8. Se retira la explicación general sobre hashes de documentos, chunks, índices, Git y credenciales. Las validaciones de fuentes, caché e idempotencia que siguen forman parte del funcionamiento de la implementación.

## 2. Falencias corregidas y decisiones pendientes

| Problema del plan original | Corrección |
|---|---|
| Considerar suficiente `sorted(set(document_ids))` para conservar idempotencia | La normalización elimina diferencias de orden, pero agregar el campo al JSON ya cambia `hash_cuerpo`. Separar forma interna y serialización histórica. |
| Cambiar solo la guarda de `document_id` de los prompts | También se debe verificar el hash autorizado de cada documento. La comparación actual con un único `source_hash` rechazaría las otras fuentes o quedaría mal debilitada. |
| Prometer prompts idénticos con un solo documento | Cambian tanto `solicitud.model_dump()` como el JSON Schema de `Referencia`. Usar proyecciones legacy explícitas y pruebas de los payloads completos. No prometer respuestas LLM idénticas byte a byte. |
| Conservar materiales después de borrar sus fuentes como si fuera una decisión acordada | Contradice §8.5, los contratos y el ciclo de borrado implementado. Mantener la política vigente como base; documentar la conservación como alternativa pendiente de decisión del equipo. |
| Añadir `retirado` al snapshot canónico y cambiarlo después | Separar procedencia inmutable del estado actual de disponibilidad. Una proyección de UI puede cambiar; el objeto canónico y sus exportaciones históricas no se reescriben por eso. |
| Acoplar fuentes por solicitud a `WORKSPACE_MAX_DOCUMENTS` | Separar límite de recursos conservados y límite de fuentes procesables en una solicitud. Subir la retención a 100 no prueba que se puedan sintetizar 100 fuentes. |
| Garantizar dos chunks por fuente sin considerar `retrieval_k` | El default actual es `retrieval_k=5`. Cinco fuentes y dos chunks por fuente necesitarían al menos diez chunks, además de suficiente presupuesto de tokens. Definir una política factible. |
| Guardar IDs para la caché multidocumental | Incluir hashes/versiones y huellas de preparación de todas las fuentes, además de los ejes actuales. IDs solos permiten resultados obsoletos. |
| Agregar `scope_hash` sin rellenar el historial existente | Backfill desde los hashes registrados en cada resultado o lectura compatible. No asignar el hash actual del documento a todos los turnos históricos. |
| Reconstruir jobs e historial desde manifiestos OCI sin definir su persistencia | Los contratos actuales no restauran colas ni historial de chat; chat/glosario son resultados locales. Reconstruir únicamente lo efectivamente persistido. Ampliar esa garantía sería otro alcance. |
| Exigir `READY` en todos los jobs del store | Ingestión necesita documentos `processing`. Mantener la distinción por tipo de trabajo. |
| Cambiar esquemas de chat/glosario antes de implementar sus servicios | Cada endpoint debe rechazar el alcance no soportado hasta estar completo. No aceptar listas para después usar solamente el primario. |
| “Defaults iguales” equivale a “sin riesgo” y presupuesto de 12k equivale a costo cero garantizado | Los defaults preservan la intención funcional, pero admisión, concurrencia y despliegue requieren pruebas. Los 12k limitan evidencia; no garantizan cuota disponible ni gasto nulo. |

Decisiones que debe confirmar el equipo antes de cerrar el contrato multidocumental:

1. Política de borrado: mantener la vigente o permitir materiales autónomos tras retirar fuentes.
2. Versión pública del contrato multidocumental y convivencia con paquetes 1.0/1.1. Una nueva variante, por ejemplo 1.2, es una propuesta; no una versión aprobada.
3. Máximo inicial de fuentes por solicitud y cobertura mínima exigida para decir que hubo síntesis cruzada.
4. Tratamiento explícito de fuentes insuficientes o contradictorias: abstención, solicitud de reducir alcance o resultado con limitaciones. No declarar cobertura completa sin evidencia.

Estas decisiones no impiden revisar ni entregar la fase independiente de límites configurables. La revisión técnica tampoco autoriza modificar unilateralmente las decisiones del equipo.

## 3. Reglas de alcance y compatibilidad

### 3.1 Entradas y alcance

- Mantener la petición histórica con `document_id` y sin `document_ids`.
- Permitir `document_ids` en el endpoint cuando su implementación esté completa. Validar IDs con las restricciones reales del proyecto; rechazar entradas vacías o inválidas antes de normalizar.
- Normalizar el conjunto como lista ordenada y sin duplicados. No usar el orden de selección como prioridad pedagógica implícita.
- Si se reciben ambos campos, exigir que `document_id` coincida con el primario canónico; rechazar contradicciones. No descartar silenciosamente un ID proporcionado.
- El primario canónico puede ser el primer ID de la lista ordenada. El foco visual de la interfaz es un estado separado y puede diferir de él.
- `documento_completo`: exactamente una fuente. `seccion`: una fuente y sección válida de esa fuente. `multi_documento`: dos o más fuentes. Rechazar combinaciones inconsistentes.
- `espacio_completo`: resolver todos los documentos `ready`, propios y no retirados en admisión. Rechazar si el conjunto es vacío o supera el máximo; no truncarlo silenciosamente. Su semántica con una sola fuente debe ser explícita.
- La selección “todos” queda congelada al admitir la operación. Un documento añadido después no se incorpora al trabajo en vuelo.
- Chat y glosario pueden compartir la normalización de fuentes, pero no deben heredar campos de alcance que sus contratos no tienen sin una decisión explícita.

### 3.2 Reintentos, caché e historial

- Mantener los reintentos de solicitudes unidocumentales existentes; agregar `document_ids` no debe provocar conflictos accidentales. Revisar la serialización de `parametros_persistidos()` y probar ese caso.
- Normalizar las solicitudes nuevas para que cambiar el orden de selección no cree otra intención. Reutilizar la misma clave con parámetros diferentes sigue siendo conflicto.
- En `espacio_completo`, guardar las fuentes resueltas al admitir la operación. Un reintento devuelve el trabajo original aunque después se añadan documentos; el worker no vuelve a expandir “todos”.
- La caché multidocumental considera todas las fuentes y versiones, además de los parámetros, modelos y configuración pertinentes que ya usa el servicio.
- El historial multidocumental corresponde al mismo espacio y conjunto de fuentes/versiones. Pasar de una fuente a varias no incorpora automáticamente otra conversación. Mantener la semántica vigente del caso unidocumental.

### 3.3 Paquetes y prompts existentes

- Mantener lectura y exportación de paquetes 1.0/1.1 sin migración destructiva.
- No añadir campos a `PaqueteEducativoBase`/`Referencia` sin revisar las uniones, `extra=forbid`, modelos estudiante, ejemplos, esquemas usados por el LLM y contratos OpenAPI.
- Una lista interna de fuentes puede derivarse de `documento_fuente` para paquetes antiguos; no obliga a reserializar el objeto guardado.
- Las exportaciones JSON históricas conservan los bytes canónicos originales. Los otros formatos también necesitan una rama de renderizado compatible: cambiar su plantilla sin versionar puede entrar en conflicto con el archivo ya cacheado.
- Mantener prompts unidocumentales solo si se verifican sistema, payload, schema y ejemplos completos. `preparar_prompts()` usa `model_dump()`, por lo que añadir defaults altera el payload aunque no se cambie el texto del sistema.
- Las respuestas de un modelo externo no tienen garantía de igualdad byte a byte. Probar contratos, citas, decisiones y fixtures deterministas.

## 4. Fase 1 — Límites configurables al iniciar el servicio

Entregable independiente del soporte multidocumental. “Configurable por `.env`” significa leído por la configuración de la aplicación; no implica recarga en caliente. Documentar reinicio/recreación del servicio según el despliegue.

### 4.1 Configuración y cableado

- [ ] Añadir `workspace_max_documents` y `workspace_max_generations` a `backend/app/config.py`, tipados y positivos, con defaults 5 y 20.
- [ ] Acordar y justificar techos operativos. Los rangos 100 y 500 del plan anterior son propuestas, no capacidades verificadas.
- [ ] Inyectar ambos valores en `RegistroOperativo` y las rutas de admisión/resumen. Evitar que store y servicio trabajen con valores diferentes.
- [ ] En `ServicioGeneraciones` usar la configuración ya existente, `self.cfg`; no hace falta introducir otro atributo `self.ajustes`.
- [ ] Revisar `main.py`, smoke real, precarga y otras instanciaciones. Mantener defaults compatibles en fixtures.
- [ ] Mantener la reserva y comprobación de límites dentro de las secciones de admisión/transacciones existentes; probar concurrencia.
- [ ] Si se baja un límite por debajo de lo ya conservado, mantener los recursos existentes y bloquear nuevas admisiones hasta liberar capacidad. No borrar recursos automáticamente.
- [ ] No cambiar el significado actual de recursos que ocupan un slot. Verificar queued/running, fallos y retiradas según las reglas existentes.

### 4.2 Despliegue, interfaz y documentación

- [ ] Actualizar `.env.example`, `.env.docker.example`, `.env.oci.example` y `.env.production.example`.
- [ ] Comprobar que los archivos/configuración de despliegue transmitan las variables y no las sobrescriban. El Compose base usa `env_file`; revisar también sus variantes.
- [ ] Mensajes ES/EN/PT sin cifras fijas y detalles del límite efectivo desde backend. Comprobar qué renderiza realmente `ErrorBanner` antes de darlo por resuelto.
- [ ] Documentar defaults, efecto de reducir/subir, reinicio requerido y consumo potencial. No afirmar que conservar más recursos gasta Gemini por sí solo: el consumo depende de nuevas ingestas/generaciones y operaciones realizadas.
- [ ] Proponer el ajuste de §8.4 sin modificar cuotas OCI, retención temporal ni límites de ingesta.

### 4.3 Aceptación

- [ ] Defaults 5/20 siguen funcionando; límites personalizados se aplican y aparecen en el resumen.
- [ ] Con límite de dos documentos, el tercero se rechaza con el código/detalle vigente del contrato; caso análogo para generaciones.
- [ ] Solicitudes concurrentes no exceden el límite. Bajar el límite no purga datos. La limpieza libera slots según la política vigente.
- [ ] Configuraciones inválidas fallan al arrancar; configuración efectiva coherente entre CLI y servidor.

## 5. Fase 2 — Núcleo multidocumental y generación

Se entrega como capacidad completa para generación después de acordar las decisiones de §2. Chat/glosario siguen rechazando multidocumental hasta su respectiva entrega. No exponer un contrato que acepte fuentes y luego las ignore.

### 5.1 Contratos y snapshots

- [ ] Implementar las reglas de §3 en `schemas/requests.py` y `schemas/pedagogical.py`.
- [ ] Definir la variante pública elegida en `schemas/responses.py`; mantener todas las variantes históricas y ambas vistas estudiante/canónica.
- [ ] Snapshot de todas las fuentes con ID, título, hash y versión. Mantener el campo primario donde se necesite compatibilidad.
- [ ] `Referencia.document_id` obligatorio en la variante multidocumental. Validar todas las citas, incluidas las anidadas dentro del contenido, no solamente la lista superior de referencias.
- [ ] Actualizar `schemas/internal.py:EvidenciaRecuperada.como_referencia()`: hoy solo proyecta `chunk_id`, página y sección. Añadir un campo al esquema sin poblar esta conversión deja las citas incompletas.
- [ ] Usar estructuras por documento para idioma, secciones disponibles/cubiertas y evidencia visual. Dos documentos pueden compartir `seccion_id` o número de página.
- [ ] No pedir al LLM que invente hashes ni otros identificadores del sistema; el backend ensambla procedencia y verifica referencias.

### 5.2 Persistencia y migración

- [ ] Revisar la versión vigente inmediatamente antes de implementar. En la base inspeccionada es 11: la siguiente sería 12 si nadie añade otra antes.
- [ ] Añadir relaciones `generation_documents` y `job_documents`, índices por documento y una representación persistida del snapshot necesario. Una relación de IDs no conserva por sí sola el hash/versiones admitidos.
- [ ] Insertar registros, snapshot, vínculos e intención idempotente de forma atómica usando los límites transaccionales existentes. No anidar transacciones incompatibles con `encolar_job()`.
- [ ] Mantener `document_id` primario durante la transición. En todas las relaciones validar ownership del workspace; las FKs por sí solas no impiden un vínculo entre espacios.
- [ ] Backfill de vínculos unidocumentales existentes. Validar filas huérfanas y no ocultar inconsistencias con un `INSERT OR IGNORE` indiscriminado.
- [ ] Añadir `scope_hash` e índice donde se usen. Reconstruir historial desde resultados guardados y sus hashes originales o mantener una ruta legacy; un campo NULL sin adaptación corta la memoria previa.
- [ ] `ingestion` mantiene su requisito `processing`; generación/chat/glosario validan `ready` y ausencia de lápidas para todas sus fuentes.
- [ ] Mantener relaciones de control necesarias para cancelar y limpiar incluso tras la retirada. No confiar en `ON DELETE CASCADE` para resolver el borrado lógico: las filas documentales pueden conservarse como lápidas.
- [ ] Recuperar relaciones de generaciones desde paquetes nuevos realmente persistidos en OCI. No prometer restaurar `job_documents`, colas o chat si esos datos no tienen una fuente durable definida.

### 5.3 Recuperación y presupuesto compartido

- [ ] Mantener `recuperar()` unidocumental compatible y añadir recuperación multidocumental sin reindexación obligatoria.
- [ ] Autorizar todas las fuentes y aplicar por documento workspace, hash original esperado y publicación/versión activa del índice. Consultar índices propios únicamente; no mezclar la colección demo con documentos privados.
- [ ] Filtro `$or` de condiciones completas por documento, con tratamiento explícito del caso vacío; nunca convertirlo en búsqueda global.
- [ ] Separar límite de fuentes por solicitud del límite de documentos conservados. Propuesta inicial: máximo pequeño, por ejemplo cinco, sujeto a la decisión del equipo y las pruebas; rechazar exceso con error de validación y detalles útiles.
- [ ] Definir `retrieval_k`, candidatos globales/por fuente y presupuesto de evidencia como restricciones compatibles. Con los defaults `k=5`, `fetch_k=15`, no garantizar dos chunks para cinco fuentes.
- [ ] Intentar cobertura de cada fuente con evidencia pertinente dentro del presupuesto. Si faltan chunks, son demasiado grandes o no respaldan la consulta, declarar la limitación; nunca forzar citas irrelevantes para cumplir una cuota.
- [ ] Presupuestar el conjunto final de evidencia serializada, IDs y metadatos con el tokenizador existente. No multiplicar los 12k por cada fuente ni acumular rondas por encima del tope.
- [ ] Deduplicar preservando procedencia: dos documentos pueden contener el mismo texto; eliminar un duplicado no debe borrar la información de sus fuentes.
- [ ] Compartir embeddings de consulta cuando técnicamente corresponda; repartir consultas y rondas sin multiplicar llamadas LLM por documento.
- [ ] Mantener deadline de 300 s, límite actual de llamadas, cuotas y cancelación. Los 12k son un techo de evidencia, no de todo el prompt ni una garantía de gratuidad.

### 5.4 Grafo, validación y catálogo

- [ ] En `graph_state.py`, guardar conjunto/snapshot autorizado y mapas por fuente; actualizar sus tipos/versiones de forma coherente con la variante pública elegida.
- [ ] Resolver “todos” en admisión; Supervisor verifica el snapshot ya resuelto, sin ampliarlo después.
- [ ] Researcher y modo básico usan el mismo alcance autorizado. Writer recibe instrucciones de síntesis solo para el caso multidocumental.
- [ ] En prompts validar para cada chunk: workspace correcto, documento seleccionado y `document_hash` igual al hash autorizado de ese documento. No basta reemplazar `!= document_id` por pertenencia a un conjunto.
- [ ] Critic/draft validation validan cada referencia contra esa evidencia; no permitir que el respaldo en B legitime una cita errónea a A. El score agregado no sustituye la comprobación de citas ni de cobertura.
- [ ] Preservar contradicciones entre fuentes y señalar diferencias. No generar consensos inexistentes ni afirmar evidencia independiente cuando el texto es duplicado.
- [ ] Actualizar Finalizer, modo básico, proyecciones estudiante y conversiones internas. Conservar que el modo básico es `no_evaluado` y no adquiere aprobación por ser multidocumental.
- [ ] Revalidar todas las fuentes al comenzar, después de I/O pertinente y antes de publicar. Si alguna cambia o se retira, no continuar con un subconjunto silencioso.
- [ ] Actualizar `generations/service.py`, `generations/catalog.py` y consultas de documentos para todas las fuentes, incluyendo estado, listado, lectura, cancelación y exports.
- [ ] Comparar snapshots según su identidad definida, preservando las validaciones actuales de integridad. No rebajar una comparación completa de fuente a solo `(id, hash)` sin considerar qué otros campos se estaban protegiendo.

### 5.5 Borrado: base vigente y alternativa separada

**Base de este plan:** mantener la política actual. Retirar cualquiera de las fuentes de una generación impide servir el material vinculado, cancela productores y agenda limpieza de sus derivados. Extender las consultas de dependencias del primario a todas las relaciones N:M para que retirar una fuente secundaria también tenga efecto.

- [ ] Revisar `documents/lifecycle.py`, `jobs/store.py`, `generations/catalog.py`, `session/recovery.py`, progreso, jobs y exports. Cambiar solamente `purgar_datos_documentales()` no evita que el ciclo actual borre los objetos de OCI.
- [ ] Conservar lápidas durables y orden de limpieza que impiden resucitar artefactos tras pérdida de disco. Preservar las respuestas 202/204 y el significado de limpieza pendiente del contrato actual.
- [ ] Probar retirada de fuente primaria/secundaria, trabajos en vuelo y fallas OCI. Comprobar que no se puedan recrear exports después de la retirada.

**Alternativa pendiente de acuerdo:** materiales autónomos tras borrar fuentes. Si el equipo la aprueba, añadir una fase específica: extractos citados verificados y persistidos por backend, disponibilidad actual fuera del canónico, tratamiento de imágenes/diagramas, retención/costos, progreso y permisos, migración de paquetes antiguos sin extractos, cambios completos del ciclo de borrado/recuperación y exportaciones. Un extracto textual no conserva automáticamente una evidencia visual. No marcar como “retirada” cualquier respuesta 404: puede indicar un recurso inexistente, ajeno o una referencia inválida.

Los paquetes y exports antiguos no se modifican para insertar `retirado=true`. Si se desean exports nuevos que reflejen disponibilidad actual, definir una representación/versión de descarga diferente y su caché. La política debe explicar que conservar extractos implica retener parte del contenido original.

### 5.6 Aceptación de fase 2

- [ ] Peticiones históricas mantienen fingerprints idempotentes y comportamiento contratado; modelos 1.0/1.1 siguen cargando y exportando.
- [ ] A+B y B+A son equivalentes; A+B y A+C no; cambio de contenido/configuración en una fuente invalida únicamente lo que corresponde.
- [ ] No hay mezcla entre workspaces, versiones de documento o índices retirados; habilitar selección múltiple no requiere reingestar las fuentes existentes.
- [ ] Fuentes ajenas, no listas, conjuntos vacíos y exceso de fuentes fallan antes de llamadas a proveedores y no dejan reservas huérfanas.
- [ ] Citas completas y correctas de varias fuentes; secciones homónimas e idiomas mixtos no colisionan.
- [ ] Migración/backfill preservan datos y memoria histórica; reinicio y reconstrucción respetan lo realmente persistido y todas las lápidas.
- [ ] Deadline, cuotas, límite de llamadas y presupuesto total de evidencia se respetan también con varias rondas.

## 6. Fase 3 — Chat, glosario, interfaz, exportaciones y demo

Entregar cada endpoint como corte completo: contratos, servicio, guardas, pruebas y cliente. Si se divide en PRs, los endpoints aún incompletos rechazan claramente multidocumental.

### 6.1 Chat

- [ ] Normalización, snapshot de fuentes, recuperación y validación por fuente.
- [ ] Historial por alcance con compatibilidad/backfill; no compartir automáticamente conversaciones de conjuntos diferentes.
- [ ] Resultado con fuentes y citas multidocumentales en la variante elegida; no mantener mapas redundantes de hashes sin validadores de consistencia.
- [ ] Retirada de cualquier fuente invalida/purga los resultados vinculados según la política vigente. Historial sigue siendo contexto conversacional, no evidencia factual.
- [ ] Mantener resultados en SQLite y las garantías actuales de recuperación; persistencia durable del historial requiere alcance y contrato propios.

### 6.2 Glosario

- [ ] `clave_cache()` con todas las fuentes, hashes/versiones y huellas de parser; conservar modelos, perfil, idioma, detector y versiones de prompts existentes.
- [ ] Ruta de caché histórica para una fuente si se promete reutilizarla. No añadir defaults al objeto `fuente` sin comprobar su efecto en el hash.
- [ ] Detección de candidatos con ubicaciones por documento y reglas explícitas para términos repetidos, polisemia y definiciones discrepantes.
- [ ] Citas, validación de fuentes y borrado extendidos al conjunto completo. Actualizar prompts/versiones cuando cambie su comportamiento.

### 6.3 Frontend y fuentes

- [ ] Actualizar tipos y clientes cuando esté disponible cada endpoint, manteniendo el cliente histórico de una sola fuente.
- [ ] Selector múltiple con todas/limpiar, estado de carga y límite efectivo. “Todas” sobre una lista paginada requiere resolución completa en servidor, no solo las filas visibles.
- [ ] Usar alcance consistente: una fuente → documento/sección; dos o más → multidocumental. Deshabilitar selección múltiple para sección.
- [ ] Enviar el primario canónico para peticiones nuevas; mantener el documento visualmente enfocado por separado.
- [ ] `SourcesDrawer` resuelve con el `document_id` de cada cita; fallback al primario solo para referencias antiguas unidocumentales.
- [ ] Identificar documento en títulos de página/sección y errores. No mostrar contenido de una fuente retirada bajo la política base.
- [ ] Revisar UI de chat, glosario, historial, comparación de adaptaciones y progreso: cualquier supuesto de “un solo documento” necesita evaluación.
- [ ] Añadir textos ES/EN/PT y mantener accesibilidad y tests de flujo.

### 6.4 Exportaciones y demo

- [ ] Para paquetes nuevos, Markdown/PDF muestran fuentes y procedencia de citas; Anki usa tags por fuente donde corresponda.
- [ ] Actualizar `exports/router.py` y su renderizado/caché según la variante del paquete. No asumir que “sin cambios de contrato” significa que no requiere cambios internos.
- [ ] Mantener bytes canónicos JSON y renderizado compatible de artefactos históricos ya cacheados.
- [ ] Mantener escenarios demo unidocumentales y equivalencia de sus parámetros; leer fixtures existentes sin reescribirlos automáticamente.
- [ ] Revisar `demo/models.py`, `catalog.py`, `publish.py`, documentos demo y comparación frontend por los nuevos esquemas/serializadores.

### 6.5 Documentación

- [ ] Registrar decisiones aprobadas en `decisiones_proyecto.md`; mantener §8.5 mientras no haya acuerdo para cambiarlo.
- [ ] Actualizar `docs/contratos-api.md`, `docs/openapi.json` y README con ejemplos ejecutables, errores y garantías reales.
- [ ] Documentar versión, compatibilidad de idempotencia/caché, límite de fuentes y diferencias entre reinicio con volumen y pérdida de disco.
- [ ] El soporte multidocumental no se considera mergeado hasta integrar la PR en `develop` y comprobar sus criterios.
- [ ] Completar la revisión de evidencias del README de §8: conservar A/B/C y agregar una ejecución D multidocumental.

## 7. Verificación y orden sugerido

Las suites siguientes existen en la base revisada; ampliar según los archivos finalmente tocados. No ejecutar tests en esta revisión de documentación como si se hubiera implementado la funcionalidad.

| Área | Pruebas relevantes |
|---|---|
| Límites/admisión/store | `test_budgets_api.py`, `test_budgets.py`, `test_jobs_store.py`, `test_document_admission.py`, `test_api_generate.py` |
| Contratos y prompts | `test_schemas.py`, `test_prompts.py`, `test_generation_contract_integration.py`, `test_openapi.py`, `test_openapi_metadata.py` |
| Recuperación RAG | `test_vectorstore.py`, `test_vectorstore_mock.py`, `test_retriever.py` |
| Agentes | `test_graph.py`, `test_supervisor.py`, `test_researcher.py`, `test_writer.py`, `test_critic.py`, `test_faithfulness.py` |
| Borrado/recuperación | `test_document_delete.py`, `test_workspace_recovery.py` |
| Chat/glosario | `test_chat.py`, `test_glossary.py`, `test_glossary_models.py` |
| Exportaciones/demo | `test_exports.py`, `test_exports_markdown.py`, `test_exports_pdf.py`, `test_exports_anki.py`, pruebas API de exports y suites demo existentes |
| Frontend | Catálogos y flujos de upload, modo básico, regresiones, chat/glosario e historial afectados |

Casos transversales imprescindibles:

1. Reintento con clave histórica tras desplegar, sin 409 accidental.
2. Orden/duplicados de selección, conflicto singular/lista y cambio de fuentes.
3. “Todo el espacio” mientras se agrega o retira un documento; reintento devuelve la intención ya admitida.
4. Un hash original igual en dos documentos/espacios no autoriza mezclar identidades.
5. Conservación de bytes originales del paquete y ausencia de conflictos en exports ya guardados.
6. Historial previo con hash antiguo no se atribuye al contenido nuevo.
7. Borrado de fuente secundaria durante consulta, generación, persistencia y descarga.
8. Presupuesto insuficiente y fuente sin evidencia: resultado limitado/rechazo explícito, sin citas inventadas.
9. Reconstrucción tras pérdida de SQLite sin resucitar recursos retirados ni prometer restaurar chat/colas.
10. Modo básico permanece sin evaluación independiente; multiagente conserva los criterios de aprobación.

Para cada implementación: tests focalizados, `ruff check` y `ruff format --check` conforme a la configuración del repo. Al completar la integración: suite backend completa, pruebas frontend y build/typecheck según los scripts vigentes de `frontend/package.json`; verificar OpenAPI y Compose cuando corresponda. Ensayo real acotado con proveedores solo bajo cuotas configuradas y alcance autorizado.

Secuencia sugerida: primero la fase 1; después acordar contrato/política y entregar generación multidocumental; luego chat, glosario y superficie completa. Dividir en issues/PRs revisables, cada uno desde `develop` actualizado, sin cambiar las ramas compartidas directamente. Crear o modificar esos recursos requiere autorización explícita del usuario.

## 8. Evidencias para el README final — conservar A/B/C y agregar D

### 8.1 Conservar los JSON existentes de A/B/C

Conservar los tres JSON de las ejecuciones unidocumentales existentes como evidencia para el README final. Implementar límites configurables y soporte multidocumental no obliga, por sí solo, a regenerarlos. Siguen acreditando las ejecuciones y adaptaciones con las que fueron producidos; no demuestran la nueva síntesis entre varias fuentes.

| Escenario | Archivo a conservar | Objeto actual en el bucket `nuevamente-contenidos-educativos` |
|---|---|---|
| A | `salida-vcn-a.json` | `demo/examples/blobs/83c654ea61ade9b3ce2dd1dbb92650b95e1eadaf9d6be729bc532dede1ed3ae9` |
| B | `salida-vcn-b.json` | `demo/examples/blobs/6528583a9bdd023db6a477353612b442af2f86c6370ef089d0e535419e65e57d` |
| C | `salida-vcn-c.json` | `demo/examples/blobs/ea0ec80d992bae489564baa236acb176f5b5b3ffdc618782722d9c575317161b` |

Los nombres de objetos son las referencias proporcionadas por el usuario. Verificar su integridad al descargarlos; esta revisión no acredita acceso al bucket ni descarga de los archivos.

- [ ] Descargar los objetos originales sin copiar/pegar ni volver a serializar su contenido. Cambiar el nombre local del archivo no modifica sus bytes.
- [ ] Verificar que el SHA-256 de cada archivo coincide con la cadena final del objeto correspondiente. Este es el alcance de la consulta original sobre hashes.
- [ ] Conservar esos bytes, sin agregar campos nuevos a los JSON existentes. Si se publica una salida con contenido diferente, tendrá su propia huella y referencia.
- [ ] Antes de cerrar el README, verificar que A/B/C siguen siendo compatibles con la versión entregada: validación del paquete, referencias y visualización/exportación por los flujos aplicables.
- [ ] Identificar cada evidencia con su escenario, propósito, fecha de ejecución y versión del sistema cuando estén disponibles. No atribuirles una ejecución del código final si fueron generadas antes; marcar los datos desconocidos sin inventarlos.
- [ ] Incluir enlaces utilizables para el lector del README, por ejemplo a los archivos incorporados al repositorio. El nombre de un objeto de un bucket privado no es por sí solo un enlace público de descarga. La incorporación/publicación se realiza posteriormente con autorización explícita.

**Cuándo regenerar A/B/C:** si los cambios los vuelven incompatibles, dejan de representar el comportamiento que el README afirma o se necesita demostrar expresamente una ejecución de esos escenarios sobre la versión final. En ese caso conservar los anteriores como registro histórico, identificar las nuevas salidas y actualizar los enlaces correspondientes. No reemplazarlos solo porque se añadió la capacidad multidocumental.

### 8.2 Agregar un escenario D multidocumental

Agregar una ejecución real sobre la versión que incorpora el soporte multidocumental. D complementa A/B/C y demuestra la capacidad nueva. Como propuesta, guardar la salida original como `salida-multidocumental-d.json`.

- [ ] Seleccionar al menos dos documentos públicos o simulados, distintos y complementarios, con información pertinente para una síntesis conjunta.
- [ ] Definir una solicitud que requiera relacionar contenido de ambas fuentes; registrar los documentos seleccionados y los parámetros de generación.
- [ ] Ejecutar el flujo real con los proveedores exigidos para la evidencia final del proyecto, dentro de las cuotas configuradas. No preparar manualmente el JSON educativo ni presentar un mock como ejecución real.
- [ ] Verificar que la salida contiene una síntesis respaldada por ambas fuentes, con referencias que identifican correctamente el documento y chunk de origen. Una lista con dos IDs no basta para demostrar síntesis multidocumental.
- [ ] Guardar los bytes originales del JSON resultante y calcular su SHA-256 para verificar la descarga. Documentar fecha, commit de ejecución, modo de generación, modelos utilizados y resultado de la revisión que corresponda, sin exponer credenciales.
- [ ] Verificar visualización de citas de ambas fuentes y al menos una exportación compatible. Registrar limitaciones reales, si las hubiera.
- [ ] Agregar D a la sección de evidencias del README junto con A/B/C, explicando qué capacidad demuestra cada uno.

D puede documentarse como evidencia independiente en el README. No exige ampliar la biblioteca demo pública ni incorporar el escenario al catálogo demo como parte de este plan; esa ampliación requeriría definir su alcance aparte.

### 8.3 Criterio de cierre de evidencias

| Evidencia | Qué acredita |
|---|---|
| A/B/C conservados y revisados | Ejecuciones unidocumentales existentes y sus adaptaciones; compatibilidad comprobada con la entrega cuando corresponda. |
| D nuevo | Ejecución real de síntesis multidocumental con citas verificables de al menos dos fuentes. |
| Pruebas de límites configurables | Aplicación de los valores de `.env`, admisión y mensajes del límite efectivo; no se deduce de los JSON A/B/C/D. |

La evidencia queda lista cuando los archivos son accesibles para el lector previsto, su integridad está verificada, las ejecuciones están identificadas con precisión y las afirmaciones del README coinciden con lo efectivamente demostrado.

## 9. Fuentes principales de esta revisión

Todos los enlaces apuntan al commit inspeccionado, para que la evidencia no cambie con la rama:

- [Recuperación vectorial y aislamiento de fuentes](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/backend/app/core/rag/vectorstore.py).
- [Serialización de solicitudes](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/backend/app/schemas/requests.py).
- [Idempotencia, historial, migraciones y purga](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/backend/app/jobs/store.py).
- [Admisión y persistencia de generaciones](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/backend/app/generations/service.py).
- [Payloads y guardas de prompts](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/backend/app/core/agents/prompts.py).
- [Proyección de evidencia a referencias](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/backend/app/schemas/internal.py).
- [Variantes actuales de paquetes](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/backend/app/schemas/responses.py).
- [Caché del glosario](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/backend/app/glossary/service.py).
- [Borrado físico y coordinación con productores](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/backend/app/documents/lifecycle.py).
- [Inmutabilidad y caché de exports](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/backend/app/exports/router.py).
- [Recuperación desde OCI](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/backend/app/session/recovery.py).
- [Decisiones vigentes, especialmente §8.4 y §8.5](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/decisiones_proyecto.md).
- [Contratos de API, retención y recuperación](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/docs/contratos-api.md).
- [Plan del equipo y definición de terminado](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/docs/plan-implementacion.md).
- [Snapshots de evidencia demo e integridad de sus JSON](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/backend/app/demo/catalog.py).
- [Publicación de evidencias demo](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f1f9ae61a5e52c7f0ef2411754ebd94738400318/backend/app/demo/publish.py).
