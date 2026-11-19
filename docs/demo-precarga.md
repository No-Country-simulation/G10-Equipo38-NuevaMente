# Precarga y recarga de fuentes demo (#49)

Este runbook prepara las tres fuentes de `documents/`: VCN, pagos y salud.
Se trata de mantenimiento administrativo offline; no expone una ruta de escritura.
Registro documental del **16/10/2026**, `develop` `f292415`: #48 y #49 estaban
integrados y cerrados; la activación y comprobación públicas se aceptan en
[#133](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/133).

Contraste por lectura del **17/10/2026**, base integrada `5988244` (#137/#138/#140):
precarga, archivos canónicos y exclusividad conservan sus procedimientos. React
es la única UI desplegable; Streamlit fue retirado. Básico/multiagente reutilizan
la preparación documental, pero no habilitan el puente demo→generación pendiente.
Los paquetes 1.0/1.1 y las descargas privadas se describen en el
[runbook](despliegue-oci.md#123-modos-recuperación-de-materiales-y-descargas).
Los ejemplos publicados siguen exigiendo material multiagente aprobado;
`completed` básico no lo sustituye. No se repitieron pruebas ni precarga.

Preparar las fuentes significa leer e indexar los tres documentos de ejemplo
una vez, para que luego la biblioteca pueda consultarlos sin repetir ese trabajo.
La preparación integrada no acredita una VM operativa. La revisión del código
vigente detectó una contradicción sobre demo→generación: fase 5 describe el
puente como probado, pero React y la autorización del backend no habilitan ese
recorrido. El [registro de #58](verificacion-issue-58.md#contradicción-demo-generación)
identifica las referencias y la decisión/seguimiento funcional pendientes del equipo.
El requisito de generar desde una fuente demo sin subir un archivo se conserva.
La carga privada y los ejemplos guardados no son equivalentes ni lo satisfacen.

## Alcance y pendientes

- Los IDs coinciden con las tarjetas existentes: `demo-redes-vcn`,
  `demo-pagos-fintech` y `demo-salud-gobernanza`.
- Originales y manifiestos viven en las claves canónicas
  `demo/{document_id}/original` y `demo/{document_id}/manifest.json`.
  La colección Chroma demo permanece separada de las fuentes privadas.
- Sólo se publica `ready` tras verificar SHA-256, metadata, cobertura, versión
  activa, inventario de chunks y persistencia condicional del manifiesto.
- VCN necesita interpretación de la página 3. Las descripciones se conservan
  como interpretaciones del modelo, separadas del texto. Si falta visión o su
  cobertura es incompleta, no se publica evidencia de disponibilidad.
- La biblioteca de fuentes funciona sin resultados. Este comando no genera ni
  simula adaptaciones educativas; la publicación de ejemplos etiquetados es #55.
  #55 ya integró los [ejemplos reales publicados](evidencia/issue-55/v2/README.md),
  con fuente, fecha y parámetros, diferenciados de una nueva ejecución.
- En el código revisado, React bloquea generar desde la selección demo y el
  backend exige un documento propio. La contradicción con fase 5 requiere una
  decisión del equipo; la precarga no elude esa autorización ni prueba el puente.
- Quedan pendientes OCI/Gemini reales en la VM y navegación por la URL pública
  tras #48, incluida la generación demo cuando esté integrada.

## Preparación

Conservar el volumen `backend_data`; contiene el índice derivado. Los originales
en OCI permiten reponerlo. Usar la misma configuración de modelos, dimensiones
y preparación que la API. Configurar cuotas explícitas de embeddings y del
modelo visual (visión o generación); modelos compartidos exigen límites iguales.

Producción requiere `APP_ENV=production`, `MOCK_OCI=0` y `MOCK_GEMINI=0`,
además de la configuración crítica vigente. El storage real conserva sus
presupuestos Always Free y tres intentos totales. No activar mocks para ocultar
un fallo real. No aprovisionar recursos ni contratar capacidad desde este comando.

Para el entorno local de Compose base, comprobar los archivos del checkout y
el parser. `--check` no escribe ni solicita OCI/Gemini; construir la imagen sí
puede descargar dependencias:

```sh
docker compose build backend
docker compose run --rm --no-deps backend python -m app.documents.preload_cli --check
```

En Windows, desde la raíz, también se puede ejecutar la comprobación local:

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m app.documents.preload_cli --check --documents-dir documents
```

`--check` no publica documentos y no acredita integración real.

## Publicar o recargar

El bloque siguiente corresponde al entorno local de Compose base, con su
configuración explícita. No es una receta de activación productiva: producción
usa base + OCI + producción, archivos canónicos y el helper de
[Caddy](../infra/caddy/README.md). Un `compose up` directo omite la compuerta de
observación del peer. El [runbook de instalación](despliegue-oci.md#10-precarga-productiva-con-un-solo-escritor)
documenta la precarga productiva y su activación/reactivación administrativa;
esa receta todavía no fue ejecutada ni aceptada sobre la VM.

Primero drenar la actividad y detener UI/API. La API y el mantenimiento toman
el mismo bloqueo de proceso sobre el volumen; no se permiten dos escritores.
El archivo `runtime.lock` permanece y no debe borrarse mientras haya procesos
activos. Usar imágenes actualizadas con ese bloqueo; ante un backend anterior,
detenerlo igualmente antes de iniciar la precarga.

```sh
docker compose stop frontend backend
docker compose run --rm --no-deps backend python -m app.documents.preload_cli
docker compose up -d backend frontend
```

El código de salida es 0 sólo si las tres fuentes quedaron `ready`, 2 si quedó
visión pendiente y 1 si falló la ejecución. Revisar la salida antes de volver a
abrir la UI. Tras un fallo, se puede reiniciar la API para consultar estados;
eso no convierte la precarga fallida en una verificación aprobada.

El mantenimiento usa el gestor común y contextos con cuotas compartidas en ese
proceso, deadline configurado (300 s de partida) y timeout por llamada de 60 s.
Los trabajos se ejecutan en serie. Cada intento de proveedor admitido registra
modelo, tokens estimados y fecha, sin prompts, fuentes, credenciales ni tokens de
sesión. Los registros operativos quedan en `demo-mantenimiento.db`, separados
de `operativo.db`: no se fabrican admisiones privadas ni se interrumpen jobs
privados sobrevivientes. Las cuotas en memoria reinician con el proceso,
igual que en la API; tener en cuenta consumos previos y límites del proveedor.
No repetir el comando en bucle para evadir cuotas.

La recarga del mismo original reutiliza la versión completa y la interpretación
compatible. Si se pierde o corrompe Chroma, reconstruye desde originales
verificados y los archivos del checkout, reemitiendo evidencia para la nueva
versión. Una interpretación persistida evita repetir visión tras un fallo de
embeddings o de publicación. Cada escritura OCI se comprueba y usa ETag:
conflicto, cancelación o lápida impiden publicar o revivir una fuente.

Un original distinto bajo el mismo ID se rechaza. Actualizar contenido exige
acordar otro ID y su tarjeta; no sobrescribir ni revertir retiradas. Si falta el
original remoto pero existe el mismo archivo verificado, la recarga lo repone.
Si el remoto está alterado, investigar el conflicto sin sobrescribirlo.

## Verificación posterior a #48

Con una sesión válida, comprobar listado y detalle en las rutas
`GET /api/demo/documents` y `GET /api/demo/documents/{id}`. Los tres documentos
deben estar `ready`, sin páginas pendientes u omitidas. Comprobar una fuente de
cada documento y una interpretación de VCN, diferenciada del texto original.

Registrar versión del checkout, modos reales, fechas y evidencia de requests
sin secretos. Confirmar desde la UI pública que seleccionar una fuente refleja
ese estado. Cuando se integre generación demo, completar el recorrido «probar
documento demo → generar» sin cargar un archivo y distinguirlo de recuperar un
ejemplo del #55. Estas verificaciones no se consideran ejecutadas por las
pruebas locales con dobles ni por `--check`.
