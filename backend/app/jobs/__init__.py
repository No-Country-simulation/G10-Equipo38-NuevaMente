"""Gestión de trabajos de generación (carril API): cola, estados, deadline,
cancelación e idempotencia.

Mapa del paquete:

- store.py: Registro operativo SQLite (issue #08): las tablas workspaces,
  sessions, documents, generations, idempotency_keys, events y
  pending_deletes con migraciones versionadas, escritor único en modo WAL,
  idempotencia atómica por workspace+operación+clave (§7.3), recuperación
  tras reinicio (running->failed/INTERRUPTED, §7.2) y tombstones de borrado
  (§8.5). Es la persistencia que el resto del paquete consume.
- manager.py: gestor de la cola con GLOBAL_HEAVY_JOB_CONCURRENCY,
  MAX_QUEUED_JOBS y GENERATION_DEADLINE_SECONDS del Apéndice A (issue #20).

Regla transversal (§14.2): un único proceso escritor; los trabajos
interrumpidos se identifican como fallidos, nunca completados. La
reconstrucción tras PÉRDIDA de volumen vive en los manifiestos OCI
(issues #14/#9): este paquete le provee invalidación de sesiones y
consulta de lápidas para que nada retirado resucite.
"""
