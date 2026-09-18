"""conftest del backend: fixtures de documentos y del doble de Gemini (issue #10).

Todo lo que vive acá está disponible (sin importar nada) en cualquier test
de backend/tests/. Los fixtures generan artefactos AL VUELO en un
directorio temporal que pytest borra después: la suite no depende de
archivos pregenerados ni ensucia el repositorio.

Inventario (lo pide el issue):

- `pdf_valido` / `pdf_cifrado` / `pdf_corrupto` / `pdf_oversize`:
  los cuatro destinos del parser (issue #11) listos para usarse.
- `crear_pdf`: fábrica para cuando un test necesita un PDF con N páginas
  o texto propio.
- `md_ejemplo` / `txt_ejemplo`: los otros dos formatos aceptados.
- `documento_demo_vcn`: el PDF real del issue #05 (la fuente común de la
  demo): sirve para pruebas de ingesta con material verdadero.
- `chunks_sinteticos`: chunks válidos del contrato interno (app/schemas),
  para retriever/vectorstore sin pasar por el parser.
- `doble_gemini`: instancia fresca del doble (ver doubles/gemini.py).
- `mock_storage`: proveedor de almacenamiento local sobre tmp_path.
- `casos_factuales`: los casos NLI versionados de §12.2.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.schemas.internal import Chunk
from app.storage.oci_storage import LocalMockStorageProvider
from doubles.gemini import DobleGemini
from fixtures.factuales import CASOS_FACTUALES

# Raíz del repo: este archivo vive en backend/tests/, dos niveles abajo.
RAIZ_REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fábrica y PDFs
# ---------------------------------------------------------------------------


def _generar_pdf(ruta: Path, paginas: int = 2, texto: str = "Documento de prueba de NuevaMente.") -> Path:
    """Genera un PDF mínimo y legible con ReportLab (dev-tool de requirements-dev).

    Por qué al vuelo y no un archivo commiteado: los bytes exactos no
    importan, sí que sea un PDF VÁLIDO; generarlo evita binarios en el repo
    y permite parameterizar páginas/texto por test.
    """
    from reportlab.pdfgen import canvas

    objeto = canvas.Canvas(str(ruta), pagesize=(595, 842))  # A4 en puntos
    for pagina in range(1, paginas + 1):
        objeto.setFont("Helvetica", 12)
        objeto.drawString(72, 770, texto)
        objeto.drawString(72, 750, f"Pagina {pagina} de {paginas}")
        objeto.showPage()
    objeto.save()
    return ruta


@pytest.fixture
def crear_pdf(tmp_path):
    """Fábrica: `crear_pdf("nombre.pdf", paginas=5, texto="...")` -> Path."""

    def _crear(nombre: str = "doc.pdf", paginas: int = 2, texto: str = "Documento de prueba de NuevaMente.") -> Path:
        return _generar_pdf(tmp_path / nombre, paginas=paginas, texto=texto)

    return _crear


@pytest.fixture
def pdf_valido(crear_pdf) -> Path:
    """PDF bien formado de 3 páginas: el caso feliz del parser (#11)."""
    return crear_pdf("valido.pdf", paginas=3, texto="VCN, subredes y gateways: documento valido.")


@pytest.fixture
def pdf_cifrado(crear_pdf, tmp_path) -> Path:
    """PDF cifrado con contraseña: §11.3 exige rechazarlo con explicación accionable.

    Se genera un PDF válido y se re-escribe cifrado con pypdf (la misma
    librería con la que el issue verificó la extracción de texto).
    """
    from pypdf import PdfReader, PdfWriter

    origen = crear_pdf("plano.pdf")
    destino = tmp_path / "cifrado.pdf"
    escritor = PdfWriter(clone_from=PdfReader(str(origen)))
    escritor.encrypt(user_password="demo-pass")
    with open(destino, "wb") as archivo:
        escritor.write(archivo)
    return destino


@pytest.fixture
def pdf_corrupto(tmp_path) -> Path:
    """PDF con firma de encabezado pero basura interna: debe fallar el parseo."""
    ruta = tmp_path / "corrupto.pdf"
    # El encabezado %PDF engaña la detección por extensión/firma; el
    # contenido ilegible es lo que el parser debe detectar y reportar.
    ruta.write_bytes(b"%PDF-1.7\n\x00\x01\x02garbage-not-a-pdf\xff\xfe")
    return ruta


@pytest.fixture
def pdf_oversize(crear_pdf, tmp_path) -> dict:
    """PDF válido que excede un límite de ejemplo PARAMETRIZABLE.

    El límite REAL lo define la configuración del issue #11 (20 MB según
    contratos-api.md); para no generar 20 MB en cada corrida, el fixture
    entrega un PDF chico junto a un `limite_bytes` menor que su tamaño:
    el test así ejercita la MISMA rama de código (rechazo por tamaño)
    sin costo. Documentado para que nadie "arregle" el fixture.
    """
    ruta = crear_pdf("grande.pdf", paginas=2)
    tamano = ruta.stat().st_size
    return {"ruta": ruta, "tamano": tamano, "limite_bytes": tamano - 1}


# ---------------------------------------------------------------------------
# Otros formatos y documentos demo
# ---------------------------------------------------------------------------


@pytest.fixture
def md_ejemplo(tmp_path) -> Path:
    """Markdown con estructura de secciones (para el chunker #12)."""
    ruta = tmp_path / "ejemplo.md"
    ruta.write_text(
        "# Guia de pagos\n\n"
        "## Autenticacion\n\nLas peticiones se firman con HMAC-SHA256.\n\n"
        "## Webhooks\n\nEl resultado llega firmado y se verifica en tiempo constante.\n\n"
        "## Errores\n\nTabla estable de codigos HTTP.\n",
        encoding="utf-8",
    )
    return ruta


@pytest.fixture
def txt_ejemplo(tmp_path) -> Path:
    ruta = tmp_path / "ejemplo.txt"
    ruta.write_text(
        "Redes VCN.\nUna VCN es una red privada regional.\n"
        "Las subredes pueden ser publicas o privadas segun su tabla de ruteo.\n",
        encoding="utf-8",
    )
    return ruta


@pytest.fixture
def documento_demo_vcn() -> Path:
    """El PDF real de la biblioteca demo (issue #05): fuente común de la demo."""
    ruta = RAIZ_REPO / "documents" / "redes_vcn_oci.pdf"
    if not ruta.is_file():
        pytest.fail(f"falta el documento demo {ruta} (issue #05)")
    return ruta


# ---------------------------------------------------------------------------
# Contratos internos sintéticos
# ---------------------------------------------------------------------------


@pytest.fixture
def chunks_sinteticos() -> list[Chunk]:
    """Chunks válidos del contrato RAG, sin pasar por parser/embeddings."""
    base = [
        ("chk_demo_001", 0, "Una VCN es la red privada que aísla los recursos en OCI.", 2, "Conceptos"),
        ("chk_demo_002", 1, "La subred pública enruta 0.0.0.0/0 hacia el Internet Gateway.", 3, "Subredes"),
        ("chk_demo_003", 2, "El NAT Gateway da salida a Internet sin exponer IP públicas.", 5, "Gateways"),
    ]
    return [
        Chunk(
            chunk_id=chunk_id,
            document_id="doc_demo_0001",
            indice=indice,
            texto=texto,
            pagina=pagina,
            seccion=seccion,
            cantidad_tokens=max(len(texto.split()), 1),
        )
        for chunk_id, indice, texto, pagina, seccion in base
    ]


# ---------------------------------------------------------------------------
# Dobles y casos factuales
# ---------------------------------------------------------------------------


@pytest.fixture
def doble_gemini() -> DobleGemini:
    """Doble fresco por test; el reset al final evita filtrar estado entre tests."""
    doble = DobleGemini()
    yield doble
    doble.reset()


@pytest.fixture
def mock_storage(tmp_path) -> LocalMockStorageProvider:
    """StorageProvider local aislado en tmp_path (MOCK_OCI=1 del entorno)."""
    return LocalMockStorageProvider(base_dir=tmp_path / "oci_mock_storage")


@pytest.fixture
def casos_factuales() -> list[dict]:
    """Casos NLI versionados de §12.2 (ver fixtures/factuales.py)."""
    return CASOS_FACTUALES
