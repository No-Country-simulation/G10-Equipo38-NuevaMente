"""Smoke nativo para la imagen ARM64; proveedor de embeddings explícitamente simulado."""

import hashlib
import sys
import tempfile
import time
from types import SimpleNamespace

from app.core.rag.embeddings import GeminiEmbeddings
from app.core.rag.vectorstore import VectorStoreChroma
from app.jobs.manager import ContextoEjecucion, CuotasModelo, CuotasProveedor
from app.schemas.internal import Chunk

modelo = "smoke-embedding"
# Sin google-genai Client ni credenciales; solo se prueba Chroma y el contrato sync.
proveedor = SimpleNamespace(es_mock=True, embed_sync=lambda textos, timeout: [[1.0, 0.0] for _ in textos])
embeddings = GeminiEmbeddings(proveedor, modelo=modelo, dimensiones=2)
ruta = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="nuevamente-chroma-")
indice = VectorStoreChroma(ruta, embeddings)
cuotas = CuotasProveedor({modelo: CuotasModelo(rpm=3, tpm=1000, rpd=3)})
contexto = ContextoEjecucion("smoke", "espacio-smoke", "indexacion", time.monotonic() + 60, cuotas=cuotas)
chunk = Chunk(
    chunk_id="chunk-smoke",
    document_id="doc-smoke",
    workspace_id="espacio-smoke",
    indice=0,
    texto="Una VCN conecta subredes.",
    cantidad_tokens=8,
    document_hash=hashlib.sha256(b"original-smoke").hexdigest(),
)
indice.indexar("espacio-smoke", "doc-smoke", [chunk], contexto=contexto)
assert indice.contar("espacio-smoke", "doc-smoke", contexto=contexto) == 1
resultado = indice.buscar("espacio-smoke", "doc-smoke", "subredes", contexto=contexto)
assert resultado[0].chunk == chunk
assert cuotas.disponibles_hoy(modelo) == 1
indice.borrar("espacio-smoke", "doc-smoke", contexto=contexto)
assert indice.contar("espacio-smoke", "doc-smoke", contexto=contexto) == 0
print("Chroma nativo: indexar, consultar y borrar OK; embeddings simulados")
