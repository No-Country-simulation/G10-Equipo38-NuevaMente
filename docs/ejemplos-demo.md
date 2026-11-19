# Ejemplos generados previamente

La biblioteca deja abrir adaptaciones ya revisadas y guardadas en OCI sin
esperar una generación nueva. Conserva fuente, fecha, parámetros y duración del
trabajo. Comparte los renderizadores del material y las fuentes de la aplicación.
El quiz empieza sin soluciones; la práctica demo no modifica el progreso privado.

## Preparación administrativa

Detener primero la API que use el mismo `DATA_DIR`. Desde la raíz, con las
credenciales existentes y ambos mocks desactivados:

```powershell
$env:PYTHONPATH = "backend"
$env:MOCK_OCI = "0"
$env:MOCK_GEMINI = "0"
.venv\Scripts\python.exe -m app.demo.prepare --check
.venv\Scripts\python.exe -m app.demo.prepare --data-dir .data/issue55-v2
```

`--check` sólo comprueba los tres archivos y la matriz, sin proveedores. La
ejecución real ingiere esos originales mediante el worker y solicita las tres
adaptaciones oficiales del mismo VCN, sus complementos y casos de pagos/salud.
No crea infraestructura ni aumenta presupuestos. Conserva el presupuesto OCI del
`DATA_DIR` habitual; reserva `.data/issue55-v2` para el registro de preparación.
El presupuesto preventivo vigente es 20.000 solicitudes/mes, con 2.000 reservadas
para limpieza. No equivale al cupo de Oracle ni acredita el consumo externo de
la tenancy. No se reinicia el contador al preparar otra versión.

El checkpoint conserva IDs y mediciones, nunca tokens, API keys ni códigos de
recuperación. Una reanudación utiliza las mismas claves de idempotencia. No
repite automáticamente pipelines fallidos o rechazados. Sólo después de resolver
una interrupción del proceso puede recuperarse ese intento explícitamente:

```powershell
.venv\Scripts\python.exe -m app.demo.prepare --data-dir .data/issue55-v2 --generate-only --solo vcn-a --recuperar-interrumpidos
.venv\Scripts\python.exe -m app.demo.prepare --data-dir .data/issue55-v2 --publish-only
```

La preparación comprueba el hash de cada documento antes de contactar a los
proveedores. Un checkpoint de v1 se conserva como histórico y no se reutiliza
con el PDF v2; usar un directorio nuevo. Los Markdown conservan LF y el PDF sus
bytes binarios para que Windows y Linux identifiquen la misma fuente.
La fuente de pagos v2 corrige el número de cabeceras del protocolo ficticio y
aclara que su helper prepara una firma sin ejecutar una llamada HTTP; su hash y
el de la versión anterior están en `documents/integracion_apis_pagos.version.json`.

`--solo` permite preparar por separado `diagrama`, `quiz-feedback` y
`evidencia-insuficiente`. Este último apunta a «15. Costos y límites», página 9
de VCN v2, aislada del glosario. No se fuerza un rechazo: cuenta lo que resuelve
el grafo con los umbrales vigentes.

Después de corregir una causa concreta puede pedirse una nueva revisión acotada:

```powershell
.venv\Scripts\python.exe -m app.demo.prepare --data-dir .data/issue55-v2 --generate-only --solo vcn-b --revision-corregida citas-1-1-2
```

La etiqueta conserva el intento anterior y fija una clave idempotente diferente.
Repetir la misma orden recupera ese intento; no vuelve a generar. Los escenarios
ya aprobados se conservan. No usarlo para repetir automáticamente un pipeline por
un fallo transitorio. Los fallos del evaluador siguen siendo fallos técnicos.

Los tres originales se limitan a los nombres de `documents/`. La publicación
verifica sus hashes, las fuentes y los paquetes canónicos aprobados. Copia bytes
sin reescribir contenido educativo a `demo/examples/blobs/{sha256}` y confirma
`demo/examples/catalog.json` al final, con escritura condicional. Una publicación
interrumpida no entrega paquetes parciales. Reanudar una publicación no llama al LLM.
Los ejemplos públicos son copias expresamente publicadas, independientes de los
espacios privados; nunca se entrega la sesión del preparador.

Una preparación incompleta publica sólo aprobados y diagnósticos de rechazos,
declara `completo=false` y termina con código 2. Los fallos técnicos no se
convierten en rechazos ni en ejemplos aprobados. Un catálogo completo existente
no puede sustituirse por uno parcial.

## Verificación realizada

Consultar [la verificación con fuentes corregidas](evidencia/issue-55/v2/README.md):
ocho adaptaciones aprobadas recuperadas desde otra sesión, quiz con ambas
correcciones, imagen original consultable y caso insuficiente rechazado.
El catálogo declara `completo=true`, con el tutorial de pagos v2 incluido.
La lectura comprobada usa la API local y OCI real; falta repetirla desde el
despliegue público para acreditar ese criterio de entrega, ahora seguido en
[#133](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/133).

El [primer registro](evidencia/issue-55/ejecucion.json) y sus capturas corresponden
a VCN v1 y se conservan sólo como evidencia histórica.

La primera preparación, sobre VCN v1, **no completa el issue 55**: A y C se aprobaron; otros
escenarios fueron rechazados o fallaron técnicamente. El ensayo de tutorial
profundo sobre el glosario de pagos fue aprobado, por lo que no demuestra el
rechazo intencional por evidencia insuficiente. No se alteró su resultado.
VCN v2 sí aísla una sección débil para ese ensayo. La nueva preparación completó
los ocho aprobados y los complementos; resta verificar la publicación en el
despliegue público. Los umbrales siguen vigentes.

Los snapshots de resultados no habilitan generar contenido nuevo desde los
IDs compartidos de la biblioteca documental del #49. Esa integración continúa
fuera de este cambio.
