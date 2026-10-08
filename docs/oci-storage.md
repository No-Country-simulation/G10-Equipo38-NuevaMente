# Preparar y verificar OCI Object Storage

Esta guía corresponde al proveedor del Issue 14. La prueba verifica almacenamiento
real de un original y un JSON técnico; no genera contenido educativo ni llama a Gemini.
El despliegue de la VM y HTTPS pertenecen a los issues 47/48.

## 1. Preparar la cuenta en la web

1. Entrar a https://cloud.oracle.com con el nombre de la cuenta cloud (tenancy),
   dominio de identidad, usuario y contraseña del correo de bienvenida.
2. Identificar y seleccionar la **home region** de la tenancy.
3. Revisar que la asignación Always Free y el consumo agregado de la cuenta permiten
   reservar capacidad para el proyecto, sin depender de créditos de prueba.
4. Crear el compartimento `nuevamente`. Copiar su OCID (identificador, no una clave).
5. Crear el bucket **privado** `nuevamente-contenidos-educativos` en ese compartimento:
   **Standard**, cifrado administrado por Oracle, versionado desactivado, sin
   replicación ni auto-tiering. La aplicación valida estos atributos y no los cambia.
6. Crear un usuario técnico y un grupo dedicado para la aplicación. La clave de un
   administrador conserva sus privilegios aunque se lo agregue a un grupo limitado;
   para acreditar permisos mínimos se usa el usuario técnico sin pertenencia a Administrators.
7. Agregarle una API signing key al usuario técnico. Descargar la clave privada y
   guardar el fragmento de configuración que muestra OCI. Nunca guardar la clave
   privada, tokens ni el archivo real de configuración en el repositorio o el chat.

Recursos oficiales:
- [Primer acceso](https://docs.oracle.com/en-us/iaas/Content/GSG/Tasks/signingin_topic-Signing_In_for_the_First_Time.htm).
- [Crear compartimento](https://docs.oracle.com/en-us/iaas/Content/Identity/compartments/To_create_a_compartment.htm).
- [Crear bucket](https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/managingbuckets_topic-To_create_a_bucket.htm).
- [Claves de firma y configuración](https://docs.oracle.com/en-us/iaas/Content/API/Concepts/apisigningkey.htm).
- [Asignaciones Always Free vigentes](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).

## 2. Permisos mínimos

Crear la política en la raíz de la tenancy. Adaptar `Default/NuevaMenteStorage`
al dominio/grupo y `nuevamente` al compartimento real. Agregar el usuario técnico
al grupo. La aplicación necesita consultar la región principal, el namespace y los
atributos del bucket, además de crear/leer/listar/actualizar/borrar sus objetos.

```text
Allow group Default/NuevaMenteStorage to inspect tenancies in tenancy where request.operation = 'ListRegionSubscriptions'
Allow group Default/NuevaMenteStorage to read objectstorage-namespaces in tenancy where request.operation = 'GetNamespace'
Allow group Default/NuevaMenteStorage to read buckets in compartment nuevamente where all {target.bucket.name = 'nuevamente-contenidos-educativos', request.operation = 'GetBucket'}
Allow group Default/NuevaMenteStorage to manage objects in compartment nuevamente where all {target.bucket.name = 'nuevamente-contenidos-educativos', any {request.permission = 'OBJECT_INSPECT', request.permission = 'OBJECT_READ', request.permission = 'OBJECT_CREATE', request.permission = 'OBJECT_OVERWRITE', request.permission = 'OBJECT_DELETE'}}
```

No concede crear buckets, cambiar clase, hacer público el bucket, habilitar
replicación/versionado, ni administrar usuarios. El bucket se provisiona con una
identidad administradora separada. Para una VM con identidad de instancia, usar un
dynamic group restringido a esa instancia y reemplazar `group` por `dynamic-group`.
Configurar `OCI_AUTH_TYPE=instance_principal`; no se necesita archivo de clave,
pero la instancia debe pertenecer a la misma región configurada.

[Permisos Object Storage](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/objectstoragepolicyreference.htm)
y [permisos IAM](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/iampolicyreference.htm).

## 3. Configuración local en Windows

Guardar los archivos en un directorio dedicado fuera del repo, por ejemplo
`C:/Users/TU_USUARIO/.oci/nuevamente/`. Crear allí `config` con el fragmento
entregado por OCI y ajustar `key_file` a la ruta de la clave privada:

```ini
[DEFAULT]
user=OCID_DEL_USUARIO_TECNICO
fingerprint=HUELLA_PUBLICA_DE_LA_CLAVE
key_file=C:/Users/TU_USUARIO/.oci/nuevamente/api_key.pem
tenancy=OCID_DE_LA_TENANCY
region=HOME_REGION_REAL
```

Copiar `.env.oci.example` a `.env.oci.local` y completar región, compartimento y
ruta de configuración. Ese archivo local está ignorado por Git. Mantener `MOCK_OCI=0`
y `NUEVAMENTE_ENV_FILE=.env.oci.local`: Compose usa esta última variable para
elegir el archivo que recibe el backend; `--env-file` por sí solo no lo reemplaza.
`OCI_ALWAYS_FREE_CONFIRMED=1` significa que el operador **ya verificó** la capacidad
gratuita disponible; no es una comprobación automática de la facturación por el SDK.
Si otros recursos consumen parte de la asignación, reducir los presupuestos.

La configuración usa inicialmente 1 GB y 5.000 solicitudes mensuales del proyecto,
por debajo de las asignaciones documentadas; no autoriza consumir el resto de la
cuenta. OCI budgets/alertas por sí solos no cortan el gasto. La auditoría agregada,
retención y cuotas de infraestructura se completan en Issue 50.

Instalar la dependencia fijada si el entorno todavía no la tiene:

```powershell
.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

## 4. Prueba real manual

Desde la raíz del repositorio, en PowerShell:

```powershell
$env:PYTHONPATH = "backend"
.venv\Scripts\python.exe -m app.tools.verify_oci --env-file .env.oci.local --report .data/oci-verification.json
```

La herramienta consulta la home region/namespace/bucket y su inventario, sube un
original de prueba y un JSON marcado `prueba_tecnica`, comprueba bytes y listado,
y elimina **solo sus claves únicas**, bajo IDs `ws_smoke_*`. El reporte solo se
escribe tras confirmar la prueba y limpieza, con fecha UTC e ID único de ejecución.
Si el archivo ya existe, la herramienta pide un nombre nuevo y no lo sobrescribe. No contiene credenciales y no sirve
como evidencia de generación pedagógica ni integración Gemini.

Si el bucket todavía no existe, `--create-bucket` habilita su creación **deliberada**
con el mismo nombre y atributos seguros; ejecutar esa variante con un perfil de
aprovisionamiento autorizado y ya verificada la asignación gratuita. Después repetir
la prueba sin ese flag con el usuario técnico de permisos mínimos. La API jamás
pasa ese flag ni crea buckets alternativos ante errores.

Un error retorna código de salida 1, no un éxito simulado. La herramienta no imprime
excepciones completas del SDK porque podrían incluir datos sensibles. Si falla la
limpieza, se informa fallo y se revisan únicamente las claves `ws_smoke_*` de esa
prueba en la consola; no borrar prefijos de espacios reales.

## 5. Docker con credenciales montadas

Agregar al archivo `config` un perfil `[DOCKER]` con los mismos datos de identidad,
pero `key_file=/run/oci/api_key.pem`. Ajustar ese nombre al archivo real descargado.
No cambia el perfil `[DEFAULT]` usado desde Windows.

```powershell
docker compose --env-file .env.oci.local -f docker-compose.yml -f docker-compose.oci.yml up --build --detach --wait
```

El overlay monta **solo el directorio dedicado**, de lectura, en `/run/oci` del
backend; no copia credenciales a imágenes ni las entrega al frontend. Si el
directorio no existe, Compose falla sin crearlo. Para la prueba aislada OCI puede
mantenerse `APP_ENV=development` y `MOCK_GEMINI=1`; producción rechaza ambos mocks
según las decisiones del proyecto. Los componentes posteriores del flujo completo
siguen dependiendo de sus propios issues.

## 6. Semántica del proveedor y límites

- `StorageProvider` conserva sus firmas y paginación `start-after`. Las claves
  canónicas están en `app/storage/object_keys.py`; el nombre original pertenece al
  manifiesto, nunca se usa como identificador de objeto.
- SDK `oci==2.187.2`, transporte síncrono, timeout configurable hasta 60 segundos.
  Cada operación tiene un intento y hasta dos reintentos transitorios. El SDK y la
  federación de identidad de instancia llevan `NoneRetryStrategy`, sin otra capa.
  OCI no utiliza `ctx.llamar` ni cuotas Gemini. Se respeta Retry-After.
- Un upload usa la misma clave, bytes, nonce y condiciones en sus hasta tres PUT.
  Verifica los bytes leídos y el nonce propio antes de devolver `StoredObject`.
  Una respuesta perdida se reconcilia sin aceptar un objeto escrito por otro proceso.
  Las lecturas de confirmación/reconciliación son solicitudes aparte y también se
  reservan; no vuelven a ejecutar el LLM ni el pipeline educativo.
- `crear_solo` no pisa objetos. `if_match` conserva el ETag opaco y protege frente a
  cambios; actualizar un objeto ausente produce `StorageNotFound`.
- `oci_budget.sqlite3` reside en `DATA_DIR` y usa transacciones SQLite para reservar
  cada intento de Object Storage/consulta de región, incluso los fallidos. El contador
  mensual se comparte entre procesos que usan el mismo volumen; no es el consumo
  global de toda la tenancy ni incluye llamadas de autenticación/metadata de instancia.
- La capacidad incorpora el inventario remoto y reserva conservadoramente el cuerpo
  completo antes de PUT. Retiene reservas de escrituras inciertas y el mayor tamaño
  confirmado por clave; un overwrite menor no libera capacidad automáticamente.
  Un DELETE confirmado retira el tamaño conocido; reservas inciertas se conservan
  hasta conciliación controlada. Esto puede rechazar antes de agotar el espacio real,
  pero evita presentar capacidad incierta como libre.
- No borrar/reinicializar el ledger en una cuenta ya usada para eludir el límite:
  ante pérdida del volumen se debe conciliar el consumo mensual antes de retomar
  llamadas reales (auditoría de Issue 50). Lecturas/listados/borrados también consumen
  solicitudes y pueden ser rechazados al agotarlas. El resumen avisa al 80%.
- OCI real nunca activa un fallback. Sin credenciales, región errónea, bucket inseguro,
  error de permisos/red o presupuesto agotado se devuelve un fallo visible.

Pruebas: `test_storage_oci.py`, `test_storage_oci_sdk.py`, `test_verify_oci.py`.
Usan SDK/HTTP simulados y claves efímeras; **no acreditan una tenancy real**.
La aceptación completa del #14 requiere además el reporte manual satisfactorio.
