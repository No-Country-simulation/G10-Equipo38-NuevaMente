"""Límites reales, integridad estructural, citas e identidad del issue 12."""

from dataclasses import replace

import pytest
from app.core.rag.chunker import ChunkingError, ConfigChunker, trocear
from app.core.rag.parser import LimitesIngesta, PaginaTexto, ResultadoParseo, parsear_archivo
from app.core.rag.tokenizer import TokenizadorBPE

pytestmark = pytest.mark.unit
BPE = TokenizadorBPE()
SMALL = ConfigChunker(tamano_objetivo=80, solapamiento=12)


def chunks(texto, extension="txt", **kwargs):
    resultado = parsear_archivo(texto.encode(), f"manual.{extension}")
    return trocear(resultado, "espacio", "documento", **kwargs)


def test_tokenizador_real_con_marcadores_literales_sin_red(monkeypatch):
    import socket

    def sin_red(*args, **kwargs):
        pytest.fail("El tokenizador debe funcionar offline")

    monkeypatch.setattr(socket.socket, "connect", sin_red)
    assert BPE.contar("hello world") == 2
    assert BPE.contar("<|endoftext|>") > 1
    assert BPE.contar("你好🙂") > len("你好🙂".split())


@pytest.mark.parametrize(
    "texto", ["palabra con explicación. " * 1500, "你好🙂" * 800, "x" * 8000], ids=["prosa", "unicode", "sin_espacios"]
)
def test_limite_real_incluye_solapamiento_y_contexto(texto):
    salida = chunks("# Contexto\n" + texto, "md")
    assert len(salida) > 1
    assert all(c.tokens == BPE.contar(c.texto) <= 825 for c in salida)
    assert all(c.texto.startswith("# Contexto\n") for c in salida)


def test_sin_solapamiento_no_pierde_caracteres_en_linea_larga():
    texto = "你好🙂abcdef" * 120
    salida = chunks(texto, config=replace(SMALL, solapamiento=0))
    assert "".join(c.texto for c in salida) == texto


def test_solapamiento_conserva_posiciones_y_no_genera_chunk_solo_repetido():
    texto = " ".join(f"concepto{i}" for i in range(200))
    salida = chunks(texto, config=SMALL)
    assert len(salida) > 2
    for anterior, actual in zip(salida, salida[1:]):
        assert actual.start_index > anterior.start_index
        prefijo = actual.texto.split("\n\n")[0]
        assert anterior.texto.endswith(prefijo)
        assert BPE.contar(prefijo) <= SMALL.solapamiento
    assert salida[-1].texto.endswith("concepto199")


def test_ids_estables_y_aislados_por_documento_config_y_tokenizador():
    resultado = parsear_archivo(b"Contenido tecnico original", "manual.txt")
    base = trocear(resultado, "ws", "doc")
    assert base == trocear(resultado, "ws", "doc")
    variantes = [
        trocear(resultado, "otro", "doc"),
        trocear(resultado, "ws", "otro"),
        trocear(replace(resultado, hash_sha256="otro"), "ws", "doc"),
        trocear(resultado, "ws", "doc", config=ConfigChunker(tolerancia_proporcion=0.2)),
        trocear(resultado, "ws", "doc", huella_config_parser="otra-version"),
        trocear(resultado, "ws", "doc", language="pt"),
    ]

    class OtraVersion(TokenizadorBPE):
        identidad = "otra-version"

    variantes.append(trocear(resultado, "ws", "doc", tokenizador=OtraVersion()))
    assert len({base[0].chunk_id, *(v[0].chunk_id for v in variantes)}) == 8
    otro_parser = parsear_archivo(b"Contenido tecnico original", "manual.txt", LimitesIngesta(max_paginas=99))
    assert base[0].chunk_id != trocear(otro_parser, "ws", "doc")[0].chunk_id


def test_citas_txt_y_md_con_bom_crlf_espacios_y_secciones():
    texto = "Introducción\n\n# Tema\n\n    cuerpo con espacios\n\n## Otro\nFinal"
    salida = chunks("\ufeff" + texto.replace("\n", "\r\n"), "md")
    assert len(salida) == 3
    assert salida[0].linea_inicio == salida[0].linea_fin == 1
    assert salida[1].linea_inicio == salida[1].linea_fin == 5
    assert salida[1].start_index == texto.index("    cuerpo")
    assert salida[1].texto == "# Tema\n    cuerpo con espacios"
    assert salida[2].section_title == "Otro"
    assert salida[2].linea_inicio == salida[2].linea_fin == 8
    assert all(c.page is None for c in salida)
    txt = chunks("\n\ntexto")[0]
    assert (txt.start_index, txt.linea_inicio, txt.linea_fin) == (2, 3, 3)


def test_paginas_no_se_mezclan():
    resultado = ResultadoParseo(
        "pdf", "manual.pdf", "hash", paginas=[PaginaTexto(1, "Primera"), PaginaTexto(2, "Segunda")]
    )
    salida = trocear(resultado, "ws", "doc")
    assert [c.page for c in salida] == [1, 2]
    assert [c.texto for c in salida] == ["Primera", "Segunda"]
    assert all(c.start_index == 0 and c.linea_inicio is None for c in salida)


def test_tablas_repiten_cabecera_sin_cortar_filas_y_con_citas():
    cabecera = "| Nombre | Valor |\n| --- | --- |\n"
    filas = [f"| fila{i} | valor{i} |" for i in range(40)]
    salida = chunks("# Tabla\n" + cabecera + "\n".join(filas), "md", config=SMALL)
    assert len(salida) > 1
    for c in salida:
        assert c.texto.startswith("# Tabla\n" + cabecera)
        assert c.tokens <= SMALL.tamano_maximo
        cuerpo = c.texto.splitlines()[3:]
        assert all(fila in filas for fila in cuerpo)
        assert c.linea_inicio == 4 + filas.index(cuerpo[0])
        assert c.linea_fin == 4 + filas.index(cuerpo[-1])
    assert [linea for c in salida for linea in c.texto.splitlines()[3:]] == filas


def test_fila_demasiado_grande_no_se_trunca():
    with pytest.raises(ChunkingError, match="fila"):
        chunks("| A | B |\n| --- | --- |\n| " + "texto " * 200 + " | x |", "md", config=SMALL)


def test_tabla_sin_bordes_externos_tambien_repite_cabecera():
    cabecera = "Nombre | Valor\n--- | ---\n"
    salida = chunks(cabecera + "\n".join(f"fila{i} | valor{i}" for i in range(40)), "md", config=SMALL)
    assert len(salida) > 1
    assert all(c.texto.startswith(cabecera) for c in salida)


def test_tabla_de_una_columna_conserva_cabecera():
    cabecera = "| Nombre |\n| --- |\n"
    salida = chunks(cabecera + "\n".join(f"| fila{i} |" for i in range(50)), "md", config=SMALL)
    assert len(salida) > 1
    assert all(c.texto.startswith(cabecera) for c in salida)


def test_cerca_con_sufijo_no_cierra_codigo_ni_crea_seccion():
    texto = "   # Tema\n```python\n```no_cierra\n# comentario\n```\nFin"
    salida = chunks(texto, "md")
    assert len(salida) == 1
    assert salida[0].section_title == "Tema"
    assert "# comentario" in salida[0].texto


@pytest.mark.parametrize("cerca", ["```", "~~~~"])
def test_codigo_preserva_cercas_indentacion_y_lineas(cerca):
    lineas = [f"    valor{i} = {i}" for i in range(50)]
    salida = chunks(f"# Código\n{cerca}python\n" + "\n".join(lineas) + f"\n{cerca}", "md", config=SMALL)
    assert len(salida) > 1
    recuperadas = []
    for c in salida:
        assert c.texto.startswith(f"# Código\n{cerca}python\n")
        assert c.texto.endswith(f"\n{cerca}")
        assert c.tokens <= SMALL.tamano_maximo
        recuperadas.extend(linea for linea in c.texto.splitlines()[2:-1] if linea)
    assert recuperadas == lineas


def test_contexto_imposible_y_cerca_vacia_no_desaparecen():
    with pytest.raises(ChunkingError):
        chunks("# " + "contexto " * 200 + "\ntexto", "md", config=SMALL)
    with pytest.raises(ChunkingError):
        chunks("```" + "lenguaje " * 200 + "\n```", "md", config=SMALL)


def test_unidad_semantica_corta_se_conserva():
    frase = "No superar 100 ms ni habilitar acceso público."
    salida = chunks("Preámbulo detallado. " * 40 + "\n\n" + frase, config=SMALL)
    assert any(frase in c.texto for c in salida)


def test_adaptadores_conservan_trazabilidad_y_omiten_nulos():
    c = chunks("# Tema\nContenido", "md", language="pt")[0]
    metadata = c.metadatos_chroma()
    assert "page" not in metadata
    assert all(type(v) in {str, int, float, bool} for v in metadata.values())
    interno = c.como_chunk_interno()
    assert interno.workspace_id == c.workspace_id
    assert interno.start_index == c.start_index
    assert interno.linea_inicio == 2
    assert interno.language == "pt"
    assert interno.tokenizer == BPE.identidad


@pytest.mark.parametrize(
    "kwargs",
    [{"tamano_objetivo": 0}, {"solapamiento": 750}, {"solapamiento": -1}, {"tolerancia_proporcion": float("nan")}],
)
def test_config_invalida(kwargs):
    with pytest.raises(ValueError):
        ConfigChunker(**kwargs)


def test_metadatos_aceptados_por_chroma_sin_embeddings_externos():
    import chromadb

    cliente = chromadb.EphemeralClient()
    coleccion = cliente.create_collection("issue12_test", embedding_function=None)
    try:
        resultado = parsear_archivo(b"Contenido", "manual.txt")
        salida = trocear(resultado, "ws1", "doc") + trocear(resultado, "ws2", "doc")
        coleccion.add(
            ids=[c.chunk_id for c in salida],
            documents=[c.texto for c in salida],
            metadatas=[c.metadatos_chroma() for c in salida],
            embeddings=[[1.0, 0.0]] * 2,
        )
        assert coleccion.count() == 2
        assert coleccion.get(where={"workspace_id": "ws1"})["ids"] == [salida[0].chunk_id]
    finally:
        cliente.delete_collection("issue12_test")
