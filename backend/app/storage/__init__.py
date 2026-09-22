"""Persistencia de contenidos (carril INF): interfaz StorageProvider y OCI.

Mapa del paquete (issue #04; el proveedor real llega con el #14):

- provider.py: el contrato StorageProvider (upload, get, get_as_text,
  list, delete) con semántica condicional (ETag, crear_solo, if_match),
  paginación por cursor y la jerarquía de errores propios
  (StorageUnavailable -> 503, StorageNotFound -> 404, StorageConflict ->
  409, StorageConfigError -> fallo visible de arranque, StorageInvalidName).
- oci_storage.py: LocalMockStorageProvider (archivos locales bajo
  .data/oci_mock_storage/ con los prefijos oficiales de §8.3 y respuestas
  rotuladas mock://) y la fábrica get_storage_provider(), que decide por
  MOCK_OCI y NUNCA convierte un fallo real en mock automáticamente (§8.2:
  con MOCK_OCI=0 y credenciales faltantes, error de arranque accionable).

Reglas de seguridad que este módulo hace cumplir (§11.2): las claves Gemini
y OCI solo existen en backend; una falla del proveedor real nunca degrada a
mock (§12.2 criterio 10); el bucket permanece privado y las descargas pasan
por la API con comprobación de ownership.
"""
