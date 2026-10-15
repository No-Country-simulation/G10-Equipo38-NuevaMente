# Evaluación pedagógica multilingüe — #60

El conjunto está definido en `docs/evidencia/issue-60/casos.json`: doce casos,
cuatro perfiles por idioma (es/en/pt), al menos tres formatos por idioma y los
tres documentos demo. Cada fuente queda fijada por SHA-256. Los cinco formatos
aparecen en el conjunto. El tamaño no permite afirmar calidad general del modelo.
Los Markdown demo usan LF y los PDF se preservan como binarios en `.gitattributes`:
sus bytes y hashes son iguales en Windows/Linux. El hash del artefacto usa su representación JSON canónica,
independiente del orden de claves o los espacios del archivo de trabajo.

## Estado y separación de responsabilidades

El PR se mantiene en draft por decisión del equipo. Aporta la matriz, validación,
preparación de materiales para dos revisores y comparación reproducible. No
incluye puntuaciones humanas inventadas ni declara que el #60 esté terminado.
Los modelos y umbrales del sistema conservan su política vigente.

La preparación puede empezar sin frontend ni VM. Para incorporar materiales se
necesitan paquetes del backend o capturas de revisión interna, su procedencia y
la configuración del proveedor. Una muestra con Gemini simulado solo verifica
las herramientas; no acredita calidad del LLM y no habilita el cierre.

## Circuito reproducible

Desde la raíz, con las dependencias del backend instaladas:

```bash
python -m backend.app.evaluation.cli validate
python -m backend.app.evaluation.cli schemas --out .data/eval60/schemas
python -m backend.app.evaluation.cli prepare --artifacts .data/eval60/artifacts --out .data/eval60/review
python -m backend.app.evaluation.cli report --artifacts .data/eval60/artifacts --reviews .data/eval60/review --out .data/eval60/report
```

Si el backend ya tiene los tres originales `ready` en el mismo espacio, la captura
automatiza generación, consulta y exportación con claves idempotentes estables:

```bash
python -m backend.app.evaluation.cli capture --api-url http://127.0.0.1:8000 --token-file .data/eval60/session-token.txt --bindings .data/eval60/bindings.json --mode real --out .data/eval60/run
```

`bindings.json` contiene únicamente `{"vcn":"ID_VCN","pagos":"ID_PAGOS","salud":"ID_SALUD"}`.
El token queda fuera de Git y solo viaja por la cabecera Authorization; no se siguen
redirecciones. Un fallo detiene la captura, conserva lo obtenido y permite retomarla.
Un rechazo público no expone borradores: se registra como incidente y requiere una
captura interna autorizada para estudiar falsos rechazos. El modo `real/mock` se
se refiere a Gemini y se declara según el backend utilizado; comprobarlo contra su configuración antes de
archivar evidencia. La CLI no puede certificar el proveedor de un servidor remoto.

Para adjudicaciones o correcciones, completar un `ResolutionLog` y pasar
`report --resolutions RUTA/resoluciones.json`. Las correcciones enlazan hashes de
artefactos por caso, con revisiones consecutivas, motivo, tipo de cambio y commit.

Cada archivo de artefacto es JSON del contrato `EvaluationArtifact`. El comando
`import-package` transforma un JSON canónico exportado por la API, sin llamar a
proveedores ni inventar una revisión del juez:

```bash
python -m backend.app.evaluation.cli import-package --case es-principiante --package .data/paquete.json --mode real --out .data/eval60/artifacts
```

El modo se declara explícitamente. El importador valida formato, perfil, idioma,
nicho, detalle y hash de fuente contra la matriz, además de la revisión canónica
aprobada. No cambia ni elimina campos del contrato de producción. Una captura
interna rechazada usa el mismo contrato de artefacto con borrador y evaluación;
permanece fuera de las rutas públicas. Fallos técnicos sin material revisable
se documentan aparte y no cuentan como falsos rechazos pedagógicos.

La generación usa los endpoints y trabajadores existentes: subir los tres
originales, esperar `ready`, ejecutar las solicitudes de la matriz (sustituir
`document_id` por el ID real) y exportar los canónicos aprobados. Debe conservar
los límites de llamadas, cuotas por modelo, retries y timeout del proyecto.
No ejecutar Gemini real hasta configurar las cuotas; la herramienta offline
no carga `.env`, no consume API y no cambia a un proveedor simulado.

## Revisión humana independiente

`prepare` crea `revisor-a.json`, `revisor-b.json` y un material por caso. Los
materiales muestran la adaptación, objetivos, referencias y documento fuente;
ocultan la decisión y puntuación del juez. El equipo custodia los artefactos
completos y no se los entrega a los revisores antes de su primera anotación.

Cada persona debe poder leer los tres idiomas, registrar su identificador real,
la fecha y confirmar que hizo la revisión humana independiente. No compartir
anotaciones hasta que ambos entreguen sus primeras respuestas. Las plantillas
empiezan sin notas y sin esa confirmación. El software valida esa declaración
y la identidad diferenciada; no puede probar por sí solo que una persona las
completó ni que trabajó de forma independiente.

La carpeta preparada también incluye `matriz.json` y copias de los tres originales
en `documents/`, comprobadas contra sus hashes antes de escribir materiales.
Entregar esa carpeta completa, conservando su estructura. Los materiales sin
generación se identifican como pendientes y no son evaluaciones terminadas.

Escala común para **claridad, adecuación al perfil, cobertura y utilidad**:

| Nota | Criterio |
|---|---|
| 1 | No cumple: incomprensible, nivel inadecuado, objetivos ausentes o material inutilizable. |
| 2 | Cumple parcialmente: necesita cambios importantes para usarlo con ese perfil. |
| 3 | Cumple: entendible, adecuado, objetivos cubiertos y utilizable con ajustes menores. |
| 4 | Cumple con claridad: estructura y explicación permiten realizar los objetivos del caso. |

Registrar también exactitud técnica, citas verificables, idioma solicitado y
justificación de la decisión. **Utilidad pedagógica y fidelidad son medidas
distintas**. Aprobar exige notas ≥3, citas/idioma correctos y ausencia de errores
técnicos; esto define la anotación humana y no modifica la política del Critic.
Ante material insuficiente usar `no_evaluable`, explicando qué falta.

No esconder desacuerdos: se conservan ambas notas y el reporte exige resolverlos
con una adjudicación explícita, razonada y fechada, preservando las anotaciones
independientes. No promediar dos decisiones contradictorias para fabricar consenso.

## Comparación y correcciones

- Falso aprobado: juez aprueba y decisión humana consolidada rechaza.
- Falso rechazado: juez rechaza y decisión humana consolidada aprueba.
- Sin dos anotaciones válidas, con desacuerdo sin resolver o juez no evaluable:
  caso pendiente, excluido del denominador de la comparación.
- Se informa muestra y frecuencias por idioma, perfil y formato; las medias de
  notas son descriptivas de esta muestra, sin extrapolación ni probabilidad de verdad.

Los artefactos y anotaciones están ligados por SHA-256. Una nueva versión requiere
nuevas anotaciones. Para cada falso aprobado guardar original, cambio concreto
(prompt, recuperación, juez o fuente), commit y artefacto reevaluado; dos revisores
deben volver a evaluar esa versión. El registro de corrección solo resuelve el
hallazgo si la reevaluación real tiene decisión humana y automática concordantes.
El reporte conserva el error inicial. No bajar umbrales ni aprobar manualmente un
resultado bloqueado para mejorar las métricas.

## Fuentes VCN y evidencia final

La matriz 1.1.0 usa VCN **v2**, corregida en el [PR #132](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/pull/132).
La trazabilidad está en `documents/redes_vcn_oci.version.json`. El conjunto previo
se conserva como histórico: no cambiar un hash dentro de un paquete aprobado para
atribuirlo a la fuente nueva. `previous_hashes` permite auditar esas corridas,
pero el comparador no las cuenta como material vigente ni prepara una revisión
con un original distinto. La interpretación del diagrama también debe verificarse.

`historico/vcn-v1/` conserva una adaptación real aprobada en #55 y los bytes exactos
de su fuente, con un material separado sin la decisión del juez. Su contenido
repite la definición incorrecta de subred pública de v1. Es un candidato para el
análisis humano de falsos aprobados; todavía no tiene anotaciones ni adjudicación.
La fidelidad al documento no garantiza que el propio documento sea técnicamente
correcto. Ese caso histórico no sustituye uno de los doce casos actuales.

Los archivos de trabajo viven en `.data/eval60/`. Solo se archivan bajo
`docs/evidencia/issue-60/` muestras públicas, reportes y anotaciones revisadas sin
tokens, códigos de recuperación, configuración privada ni prompts completos.
`report --require-complete` devuelve error mientras haya bloqueos; el reporte
provisional también se genera y deja explícito qué falta.

El cierre requiere ≥12 casos reales anotados por dos personas, todos los falsos
aprobados corregidos/reevaluados, reporte final archivado y revisión del equipo.
