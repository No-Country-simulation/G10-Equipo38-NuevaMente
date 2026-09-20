"""Gestión de trabajos de generación (carril API): cola, estados, deadline,
cancelación e idempotencia.

Mapa del paquete:

- store.py: Registro operativo SQLite (issue #08): las tablas workspaces,
  sessions, documents, generations, idempotency_keys, events y
  pending_deletes con migraciones versionadas, escritor único en modo WAL,
  idempotencia atómica por workspace+operación+clave (§7.3), recuperación
  tras reinicio (running->failed/INTERRUPTED, §7.2) y tombstones de borrado
  (§8.5). Es la persistencia que el resto del paquete consume.
- manager.py (issue #20, implementado): GestorTrabajos con worker dedicado
  (14.2), ranura global 1 + cola <=5 + 1 por espacio, deadline y espera
  maxima de 7.5, cancelacion cooperativa, reintentos con backoff+jitter y
  CuotasProveedor RPM/TPM/RPD por modelo contando reintentos. La tabla
  `jobs` (migracion v2 del store) persiste estados/resultados y los eventos
  alimentan el SSE de #31. Los endpoints HTTP llegan con #31/#19 junto a
  las sesiones de #9 (contratos-api exige autenticacion y ownership).

Regla transversal (§14.2): un único proceso escritor; los trabajos
interrumpidos se identifican como fallidos, nunca completados. La
reconstrucción tras PÉRDIDA de volumen vive en los manifiestos OCI
(issues #14/#9): este paquete le provee invalidación de sesiones y
consulta de lápidas para que nada retirado resucite.
"""
