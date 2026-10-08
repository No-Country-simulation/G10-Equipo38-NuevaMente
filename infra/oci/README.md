# Preparación de la VM — Issue 47

El objetivo vigente es Ubuntu 24.04 ARM64, VM.Standard.A1.Flex con 2 OCPU y
12 GB en la home region, respetando la asignación Always Free de la cuenta.
Referencia: `decisiones_proyecto.md` §9 y
`docs/issues/fase-5-despliegue-endurecimiento.md`.

- `provision_vm.py`: consulta inventario, cuotas y capacidad; `--apply` crea o retoma
  únicamente la configuración A1 elegida, con diario local y tokens idempotentes.
- `bootstrap.sh`: preparación de una VM nueva, Docker/Compose, actualizaciones
  automáticas de seguridad y SSH por clave sin acceso root ni contraseña.
- `verify_host.sh`: comprobaciones que se ejecutan con sudo dentro de la VM.
  La red OCI, el acceso desde otra IP y la captura de gratuidad se verifican aparte.

La consulta del aprovisionador se probó contra OCI real. Las guardas y la
creación/reanudación tienen pruebas con OCI simulado; los scripts pasan `bash -n`.
Todavía no se ejecutaron en una VM real ni completan la provisión del #47.
La aplicación, Caddy y sus credenciales se despliegan en Issue 48.

## Bloqueo observado el 8 de octubre de 2026

La consulta real `CreateComputeCapacityReport` en `sa-vinhedo-1`, para 2 OCPU y
12 GB, devolvió `OUT_OF_HOST_CAPACITY` en el único dominio de disponibilidad.
El inventario no tenía instancias activas ni volúmenes boot/block consumidos;
las cuotas permitían la configuración, pero no había capacidad física.
No se creó una VM ni se cambió a otra región o configuración paga.
El reporte local de esta consulta está en `.data/oci-47-preflight-43e0e819de1d483e99c1256fe4dc46ac.json`;
es temporal y no sustituye una nueva consulta antes de aprovisionar.

El #47 permanece pendiente: faltan provisión real, comprobación de ARM64/Docker,
prueba SSH desde una IP no autorizada y captura de consola con gratuidad/costo cero.


## Consulta y provisión

Usar Python 3.11 y `oci==2.187.2` (ya fijado en `backend/requirements.txt`).
La herramienta no carga `.env`, no llama a Gemini y no monta credenciales en la VM.
La configuración del **operador** necesita permisos de lectura del inventario de la tenancy
y creación de Compute/red en el compartimento elegido. La identidad del backend sigue
siendo el usuario técnico limitado al bucket del #14.

Desde la raíz, en PowerShell, completar valores locales; las claves permanecen fuera de Git:

```powershell
$configuracionOci = "C:/ruta/fuera-del-repo/.oci/config"
$compartimentoOci = "ocid1.compartment.oc1..REEMPLAZAR"
.venv/Scripts/python.exe -B infra/oci/provision_vm.py --config-file $configuracionOci --compartment-id $compartimentoOci
```

Sin `--apply` consulta la asignación y el estado de capacidad. El reporte queda en
`.data/oci47/preflight-*.json`. Son evidencias de ese momento, no autorizan por sí solas
una creación posterior. Cada ejecución vuelve a consultar OCI.

Cuando haya capacidad, preparar un par SSH Ed25519 fuera del repositorio, con la clave
privada protegida por permisos del usuario; solo se envía la `.pub` a OCI.
`--confirm-always-free` expresa que el operador revisó la franquicia mensual,
el consumo previo de A1 y los discos de la tenancy. Las cuotas de servicio pueden ser
mayores que la franquicia gratuita y **no** autorizan aumentar 2 OCPU / 12 GB.

```powershell
$ipAdministracion = "TU_IPV4_PUBLICA/32"
$clavePublicaSsh = "C:/ruta/fuera-del-repo/id_ed25519.pub"
.venv/Scripts/python.exe -B infra/oci/provision_vm.py --config-file $configuracionOci --compartment-id $compartimentoOci --admin-cidr $ipAdministracion --ssh-public-key $clavePublicaSsh --confirm-always-free --apply
```

La receta fija 2 OCPU / 12 GB, Ubuntu 24.04 ARM64 oficial, boot de 50 GB con
rendimiento Balanced (10 VPU/GB), sin Marketplace, AI Enterprise, reservas de
capacidad ni políticas de backup. Solo usa la home region. Cuenta también instancias
A1 detenidas y discos sin instancia; rechaza superar 2 OCPU / 12 GB o 200 GB de discos
combinados. La capacidad se vuelve a comprobar inmediatamente antes del lanzamiento.

El diario operativo es `.data/oci47/state.json`, separado de los reportes. Antes de
crear se guarda el token idempotente; al retomar se verifican IDs, pertenencia, tags,
reglas y parámetros contra OCI. Un token vencido sin confirmación o una configuración
diferente exigen revisión manual. El bloqueo local impide ejecuciones simultáneas con
ese diario. Conservarlo para retomar; no reemplazarlo con un snapshot antiguo.

## Red y host

- VCN/subred propias: la subred usa exclusivamente la lista de seguridad creada por
  esta receta, evitando el SSH abierto de la lista predeterminada de OCI.
- Ingreso TCP: 22 desde la IPv4 administrativa /32; 80/443 para el proxy del #48.
  ICMP tipo 3/código 4 permite mensajes de MTU. Egreso habilitado para actualizaciones,
  Docker, Gemini y OCI. No se habilita IPv6 ni se agregan NSG a la VNIC.
- Ubuntu: clave pública, sin root/contraseña por SSH; actualizaciones de seguridad
  automáticas sin reinicio automático. El usuario `ubuntu` conserva sudo.
- Firewall del host: protege INPUT y DOCKER-USER; se reaplica al arrancar/reiniciar
  Docker. El tráfico nuevo desde la interfaz exterior no llega a puertos publicados
  por contenedores. Caddy corre en el host en #48.
- `backend_data` ya está definido en `docker-compose.yml`: su driver local almacena
  datos en el disco de la VM. En #48 usar un nombre estable de proyecto Compose
  (por ejemplo `nuevamente`) para conservar el mismo volumen entre actualizaciones.
- El montaje OCI de solo lectura está definido en `docker-compose.oci.yml` (#14).
  Se configura con el usuario técnico al desplegar #48; el config del operador no
  se copia al host ni al backend.

Si cambia la IP de administración, actualizar de forma coordinada la lista OCI y
el firewall del host. La receta rechaza cambios silenciosos de sus parámetros;
no ampliar SSH a `0.0.0.0/0` para resolver un bloqueo de acceso.

## Verificación pendiente en la VM

`RUNNING` de OCI no acredita que cloud-init ni Docker hayan terminado. Esperar
`cloud-init status --wait --long`, comprobar la clave de host por un canal OCI de
confianza antes de aceptarla en SSH, y ejecutar:

```bash
sudo bash infra/oci/verify_host.sh
```

Además comprobar persistencia de las reglas al reiniciar Docker y el servicio SSH.
Desde la IP administrativa, SSH debe conectar. Desde otra red/IP debe fallar la
conexión TCP al 22; una prueba desde la misma conexión no satisface ese criterio.
Registrar también que 8000/8501 no son accesibles directamente.

La evidencia de costo exige la consola OCI con la instancia A1 y su asignación/costo
cero. No publicar claves ni configuración privada en capturas. Estos controles siguen
pendientes porque no existe la VM; el #47 no se marca completado por tests simulados.

Fuentes: [Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
[reglas de red OCI](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securityrules.htm),
[instalación oficial de Docker](https://docs.docker.com/engine/install/ubuntu/).


### Reconsulta y último intento del 8 de octubre

Tres consultas de capacidad durante esta continuación devolvieron
`OUT_OF_HOST_CAPACITY`. El último intento ejecutó `--apply` con la configuración
autorizada y se detuvo en la guarda previa: no creó VCN/subred, VM ni discos.
Las cuotas efectivas consultadas permitían 2 OCPU / 12 GB; el inventario seguía
sin A1 ni volúmenes. Esto distingue falta de capacidad física de falta de cuota.
Reportes locales de esta continuación: `.data/oci47/preflight-*.json`.
