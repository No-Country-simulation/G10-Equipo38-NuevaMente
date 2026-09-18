"""Tests de los fixtures de documentos (issue #10).

Garantizan que el material de prueba es lo que dice ser ANTES de que el
parser (issue #11) lo consuma: si un fixture se corrompe, falla acá con
un mensaje propio y no dentro del parser con un diagnóstico engañoso.
"""

import pytest

pytestmark = pytest.mark.unit


def test_pdf_valido_es_legible_con_herramienta_independiente(pdf_valido):
    """pypdf (la herramienta con la que el issue #05 verificó la extracción)
    debe poder leerlo y extraer su texto."""
    from pypdf import PdfReader

    lector = PdfReader(str(pdf_valido))
    assert len(lector.pages) == 3
    texto = lector.pages[0].extract_text()
    assert "VCN" in texto


def test_pdf_cifrado_queda_realmente_cifrado(pdf_cifrado):
    """El fixture debe exigir contraseña: es la condición que #11 rechaza."""
    from pypdf import PdfReader

    lector = PdfReader(str(pdf_cifrado))
    assert lector.is_encrypted


def test_pdf_corrupto_tiene_firma_pero_no_estructura(pdf_corrupto):
    contenido = pdf_corrupto.read_bytes()
    assert contenido.startswith(b"%PDF")  # engaña la detección por firma...
    assert b"garbage-not-a-pdf" in contenido  # ...pero no es parseable


def test_pdf_oversize_expone_el_limite_parametrizable(pdf_oversize):
    """El rechazo por tamaño se prueba con límite menor al real (documentado
    en el fixture): misma rama de código, sin generar 20 MB por corrida."""
    assert pdf_oversize["tamano"] > pdf_oversize["limite_bytes"]


def test_md_y_txt_de_ejemplo_tienen_contenido(md_ejemplo, txt_ejemplo):
    assert "## Webhooks" in md_ejemplo.read_text(encoding="utf-8")
    assert "VCN" in txt_ejemplo.read_text(encoding="utf-8")


def test_documento_demo_de_la_biblioteca_existe_y_es_pdf(documento_demo_vcn):
    contenido = documento_demo_vcn.read_bytes()
    assert contenido.startswith(b"%PDF")
    from pypdf import PdfReader

    lector = PdfReader(str(documento_demo_vcn))
    assert 8 <= len(lector.pages) <= 15  # rango exigido por el issue #05


def test_chunks_sinteticos_respetan_el_contrato_interno(chunks_sinteticos):
    """Los chunks sintéticos son válidos según el modelo del contrato (#03)."""
    assert [chunk.indice for chunk in chunks_sinteticos] == [0, 1, 2]
    assert all(chunk.cantidad_tokens > 0 for chunk in chunks_sinteticos)
    ids = [chunk.chunk_id for chunk in chunks_sinteticos]
    assert len(set(ids)) == len(ids)  # IDs únicos: regla de §15/issue #12


def test_el_entorno_de_test_quedo_configurado():
    """El conftest raíz fija los mocks ANTES de importar la aplicación."""
    import os

    assert os.environ["MOCK_OCI"] == "1"
    assert os.environ["MOCK_GEMINI"] == "1"
    assert os.environ["APP_ENV"] == "test"
