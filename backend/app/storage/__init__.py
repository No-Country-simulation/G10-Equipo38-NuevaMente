"""Persistencia de contenidos (carril INF): interfaz StorageProvider y OCI.

Módulos previstos (§15):
- provider.py: interfaz abstracta StorageProvider + proveedor mock explícito
  para desarrollo/CI con MOCK_OCI=1 (issue #04).
- oci_storage.py: proveedor real de OCI Object Storage, bucket
  "nuevamente-contenidos-educativos", Always Free (issue #14).

Reglas: las claves Gemini/OCI solo existen en backend (§11.2); una falla del
proveedor real nunca degrada silenciosamente a mock (§12.2 criterio 10); el
bucket permanece privado y las descargas pasan por la API con comprobación de
ownership.
"""
