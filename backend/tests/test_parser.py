"""Tests del parser y validación de documentos (issue #11).

Verificación exacta del issue: `pytest backend/tests/test_parser.py` con los
fixtures generados del issue #10 (PDF válido, corrupto, cifrado, oversize).

Criterio por criterio:

1. Los 3 documentos demo (#05) se parsean con metadatos de página/sección.
2. PDF cifrado, archivo corrupto y archivo de 25 MB → rechazados con error
   accionable y código estable.
3. Un PDF de 150 páginas se rechaza por el límite de páginas ANTES de
   extraer texto.
4. Texto pegado se convierte en documento TXT persistible.

Además: límites con valores pequeños inyectados (misma rama de código sin
archivos gigantes), nunca truncar en silencio, cobertura informada,
escaneados derivados al contrato visual (#30) en vez de rechazados, y
nombre subido usado solo como etiqueta.
"""

import pytest
from app.core.rag.parser import (
    LimitesIngesta,
    ParserError,
    parsear_archivo,
    parsear_texto_pegado,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Criterio 1: los documentos demo de #05 parsean con metadatos
# ---------------------------------------------------------------------------


def test_pdf_demo_vcn_parsea_con_paginas_y_deriva_el_diagrama(documento_demo_vcn):
    """El PDF VCN (fuente común de la demo) parsea página a página.

    Su página 4 es el diagrama vectorial: PyPDF no extrae texto de ella,
    así que el parser la reporta como página VISUAL pendiente de #30 y NO
    declara el documento ready (§4.2: no declarar ready lo incompleto).
    """
    resultado = parsear_archivo(documento_demo_vcn.read_bytes(), documento_demo_vcn.name)

    assert resultado.extension == "pdf"
    assert 8 <= len(resultado.paginas) + len(resultado.paginas_visuales) <= 15
    assert resultado.paginas, "el PDF VCN tiene páginas con texto"
    assert all(pagina.numero >= 1 for pagina in resultado.paginas)
    assert resultado.cantidad_tokens > 0
    assert resultado.hash_sha256.startswith(
        ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "b", "c", "d", "e", "f")
    )
    assert resultado.cobertura  # informe de cobertura presente (§4.1)
    # El concepto clave del documento debe estar en el texto extraído.
    texto_total = "\n".join(pagina.texto for pagina in resultado.paginas).lower()
    assert "vcn" in texto_total and "gateway" in texto_total


def test_md_demo_de_pagos_parsea_con_secciones():
    """El MD Fintech (#05) parsea con secciones y rangos de línea."""
    ruta = "documents/integracion_apis_pagos.md"
    resultado = parsear_archivo(open(ruta, "rb").read(), "integracion_apis_pagos.md")

    assert resultado.extension == "md"
    titulos = [seccion.titulo for seccion in resultado.secciones]
    assert "3. Autenticación" in titulos and "6. Webhooks: la fuente de verdad del resultado" in titulos
    # Metadatos de origen: cada sección sabe su rango de líneas (§4.3).
    for seccion in resultado.secciones:
        assert 1 <= seccion.linea_inicio <= seccion.linea_fin
        assert seccion.texto.strip()
    assert resultado.estado_documento.value == "ready"


def test_md_demo_de_salud_parsea():
    resultado = parsear_archivo(open("documents/gobernanza_datos_salud.md", "rb").read(), "gobernanza_datos_salud.md")
    assert resultado.extension == "md"
    assert any("Clasificación de los datos" in s.titulo for s in resultado.secciones)
    assert resultado.estado_documento.value == "ready"


# ---------------------------------------------------------------------------
# Criterio 2: rechazos accionables con código estable
# ---------------------------------------------------------------------------


def test_pdf_cifrado_se_rechaza_con_codigo_estable(pdf_cifrado):
    with pytest.raises(ParserError) as info:
        parsear_archivo(pdf_cifrado.read_bytes(), "cifrado.pdf")
    assert info.value.codigo.value == "CIFRADO"
    assert "sugerencia" in info.value.detalles  # accionable: qué hacer


def test_pdf_corrupto_se_rechaza_con_codigo_estable(pdf_corrupto):
    with pytest.raises(ParserError) as info:
        parsear_archivo(pdf_corrupto.read_bytes(), "corrupto.pdf")
    # El fixture tiene firma %PDF válida pero basura dentro: PyPDF explota
    # al leer la estructura → CORRUPTO (no FORMATO_NO_SOPORTADO).
    assert info.value.codigo.value in ("CORRUPTO", "EXTRACCION_INSUFICIENTE")


def test_archivo_de_25mb_se_rechaza_por_tamano():
    """Criterio literal: 25 MB > 20 MB → DEMASIADO_GRANDE con números."""
    contenido = b"%PDF-1.7\n" + b"0" * (25 * 1024 * 1024)
    with pytest.raises(ParserError) as info:
        parsear_archivo(contenido, "enorme.pdf")
    assert info.value.codigo.value == "DEMASIADO_GRANDE"
    assert info.value.detalles["limite"] == 20 * 1024 * 1024
    assert info.value.detalles["recibido"] == len(contenido)


def test_oversize_con_limite_parametrizable(pdf_oversize):
    """El fixture trae un límite menor al real: misma rama de código,
    sin generar 20 MB por corrida (documentado en el conftest de #10)."""
    with pytest.raises(ParserError) as info:
        parsear_archivo(
            pdf_oversize["ruta"].read_bytes(),
            "grande.pdf",
            LimitesIngesta(max_bytes_pdf=pdf_oversize["limite_bytes"]),
        )
    assert info.value.codigo.value == "DEMASIADO_GRANDE"


def test_extension_mintida_se_rechaza_por_firma(tmp_path):
    """§11.3: la extensión no es prueba. Un .pdf que es un PNG, no pasa."""
    contenido_fake = b"\x89PNG\r\n\x1a\n" + b"imagen" * 100
    with pytest.raises(ParserError) as info:
        parsear_archivo(contenido_fake, "trucho.pdf")
    assert info.value.codigo.value == "FORMATO_NO_SOPORTADO"


def test_txt_con_bytes_binarios_se_rechaza(tmp_path):
    with pytest.raises(ParserError) as info:
        parsear_archivo(b"texto\x00\x01\x02binario", "trucho.txt")
    assert info.value.codigo.value == "BINARIO"


def test_txt_no_utf8_se_rechaza():
    # Latin-1 con acentos: bytes inválidos como UTF-8.
    with pytest.raises(ParserError) as info:
        parsear_archivo("computación".encode("latin-1"), "viejo.txt")
    assert info.value.codigo.value == "FORMATO_NO_SOPORTADO"


# ---------------------------------------------------------------------------
# Criterio 3: límite de páginas ANTES de extraer
# ---------------------------------------------------------------------------


def test_pdf_de_150_paginas_se_rechaza_por_limite_de_paginas(crear_pdf):
    """El rechazo ocurre por NÚMERO de páginas, sin extraer texto:
    contar páginas es la primera cosa que hace el parser tras validar
    firma/cifrado (criterio del issue)."""
    pdf_150 = crear_pdf("largo.pdf", paginas=150)
    with pytest.raises(ParserError) as info:
        parsear_archivo(pdf_150.read_bytes(), "largo.pdf")
    assert info.value.codigo.value == "LIMITE_PAGINAS"
    assert info.value.detalles == {"limite": 100, "recibido": 150, "unidad": "paginas"}


def test_limite_de_tokens_nunca_trunca_en_silencio():
    """Texto grande con límite de tokens inyectado pequeño → rechazo
    completo (100% del documento o nada, §4.1)."""
    texto = ("palabra técnica " * 5000).encode("utf-8")  # ~75k tokens estimados
    with pytest.raises(ParserError) as info:
        parsear_archivo(texto, "largo.txt", LimitesIngesta(max_tokens_extraidos=100))
    assert info.value.codigo.value == "LIMITE_TOKENS"


# ---------------------------------------------------------------------------
# Criterio 4: texto pegado → documento TXT persistible
# ---------------------------------------------------------------------------


def test_texto_pegado_se_convierte_en_documento_txt():
    resultado = parsear_texto_pegado(
        "Notas de redes",
        "Una VCN es una red privada.\nLas subredes pueden ser públicas o privadas.",
    )
    assert resultado.extension == "txt"
    assert resultado.titulo_etiqueta == "Notas de redes"
    assert resultado.estado_documento.value == "ready"
    assert resultado.secciones and resultado.secciones[0].texto.startswith("Una VCN")
    assert len(resultado.hash_sha256) == 64  # sha256 hex completo
    assert "seccion(es)" in resultado.cobertura


def test_hash_de_texto_pegado_coincide_con_archivo_txt_equivalente(tmp_path):
    """§4.5 (dedupe por hash): mismo contenido → mismo hash, venga de un
    archivo .txt o del área de pegar texto."""
    contenido = "mismo contenido exacto"
    por_archivo = parsear_archivo(contenido.encode("utf-8"), "a.txt")
    por_pegado = parsear_texto_pegado("a", contenido)
    assert por_archivo.hash_sha256 == por_pegado.hash_sha256


def test_texto_pegado_vacio_da_error_claro():
    with pytest.raises(ParserError):
        parsear_texto_pegado("título", "   ")


# ---------------------------------------------------------------------------
# Escaneados → contrato visual (#30) y cobertura
# ---------------------------------------------------------------------------


def test_pdf_escaneado_puro_no_se_rechaza_queda_pendiente_de_vision(crear_pdf, monkeypatch):
    """Criterio del issue: un PDF legible pero sin texto (escaneo) NO se
    rechaza por texto vacío: se deriva al contrato visual de #30 y queda
    processing, sin declararse ready."""
    from types import SimpleNamespace
    from unittest.mock import patch

    pdf = crear_pdf("escaneado.pdf", paginas=2)
    # Simular el escaneo: extract_text devuelve cadena vacía en todo el PDF.
    # (SimpleNamespace en vez de object(): object() no acepta atributos.)
    paginas_falsas = [SimpleNamespace(extract_text=lambda: "") for _ in range(2)]
    with patch("app.core.rag.parser.PdfReader") as lector_falso:
        instancia = lector_falso.return_value
        instancia.is_encrypted = False
        instancia.pages = paginas_falsas
        resultado = parsear_archivo(pdf.read_bytes(), "escaneado.pdf")

    assert resultado.requiere_vision is True
    assert resultado.paginas == []
    assert resultado.paginas_visuales == [1, 2]
    assert resultado.estado_documento.value == "processing"
    assert "issue #30" in resultado.detalle_estado


def test_pdf_valido_reporta_cobertura(pdf_valido):
    # El fixture es deliberadamente mínimo (~44 tokens): bajar el mínimo
    # inyectado para probar la rama ready, no la de extracción insuficiente.
    limites = LimitesIngesta(minimo_tokens_documento=5)
    resultado = parsear_archivo(pdf_valido.read_bytes(), "valido.pdf", limites)
    assert resultado.estado_documento.value == "ready"
    assert "Paginas procesadas" in resultado.cobertura
    assert "3 de 3" in resultado.cobertura


def test_nombre_solo_es_etiqueta_nunca_ruta(pdf_valido, tmp_path):
    """§11.3: el nombre con ruta maliciosa no toca el filesystem: se usa
    solo como etiqueta y se limpia para mensajes."""
    nombre_malicioso = "../../etc/passwd.pdf"
    limites = LimitesIngesta(minimo_tokens_documento=5)
    resultado = parsear_archivo(pdf_valido.read_bytes(), nombre_malicioso, limites)
    assert resultado.titulo_etiqueta  # aceptado como etiqueta
    assert not (tmp_path / nombre_malicioso).exists()  # nunca escribió nada
