# Preparación de la VM — Issue 47

El objetivo vigente es Ubuntu 24.04 ARM64, VM.Standard.A1.Flex con 2 OCPU y
12 GB en la home region, respetando la asignación Always Free de la cuenta.
Referencia: `decisiones_proyecto.md` §9 y
`docs/issues/fase-5-despliegue-endurecimiento.md`.

- `bootstrap.sh`: preparación de una VM nueva, Docker/Compose, actualizaciones
  automáticas de seguridad y SSH por clave sin acceso root ni contraseña.
- `verify_host.sh`: comprobaciones que se ejecutan con sudo dentro de la VM.
  La red OCI, el acceso desde otra IP y la captura de gratuidad se verifican aparte.

Estos archivos son preparación inicial: se verificó su sintaxis con `bash -n`,
pero no se ejecutaron en una VM real ni completan la provisión del #47.
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
