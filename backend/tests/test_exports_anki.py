"""#45: archivos reales, importador oficial de Anki, sin red/colecciones personales."""

import csv
import io
import json
import sqlite3
import zipfile
from copy import deepcopy

import pytest
import test_quiz_progress as progreso
from anki.collection import Collection
from anki.import_export_pb2 import ImportAnkiPackageRequest, ImportCsvRequest
from app.exports.anki import campo_html, etiquetas, exportar_apkg, exportar_texto, guid, nombre_mazo
from app.exports.router import ServicioExportaciones
from app.schemas.responses import PedagogicalOutput
from app.storage.object_keys import clave_export
from test_schemas import FLASHCARDS, salida_valida

pytestmark = pytest.mark.unit
espacio_exportaciones = progreso.estudio


@pytest.fixture
def paquete():
    mazo = deepcopy(FLASHCARDS)
    mazo["items"] = [
        {
            **deepcopy(mazo["items"][0]),
            "id": "primera",
            "frente": '=HYPERLINK("https://ejemplo","ação")',
            "dorso": '<script>malicioso()</script>\n"Red", subred\t& conexión [sound:externo.mp3]',
            "etiquetas": ["redes OCI", "ação", "-etiqueta", "=formula", "redes OCI"],
        },
        {**deepcopy(mazo["items"][0]), "id": "segunda", "frente": "Mismo frente", "dorso": "Primera respuesta"},
        {**deepcopy(mazo["items"][0]), "id": "tercera", "frente": "Mismo frente", "dorso": "Segunda respuesta"},
    ]
    return salida_valida(mazo)


@pytest.fixture
def coleccion(tmp_path):
    col = Collection((tmp_path / "collection.anki2").as_posix())
    yield col
    col.close()


@pytest.mark.parametrize("formato,separador", [("csv", ","), ("tsv", "\t")])
def test_utf8_separadores_comillas_formula_html_y_columnas(paquete, formato, separador):
    texto = exportar_texto(paquete, formato).decode("utf-8")
    assert "#html:true\n#tags column:3\n#guid column:4\n#deck column:5" in texto
    filas = list(csv.reader([linea for linea in texto.splitlines() if not linea.startswith("#")], delimiter=separador))
    assert len(filas) == 3 and all(len(f) == 5 for f in filas)
    assert filas[0][0] == campo_html(paquete.contenido_adaptado.items[0].frente)
    assert filas[0][1] == campo_html(paquete.contenido_adaptado.items[0].dorso)
    assert "&lt;script&gt;" in filas[0][1] and "[sound:" not in filas[0][1]
    assert all(not celda.lstrip().startswith(("=", "+", "-", "@")) for fila in filas for celda in fila)
    assert "ação" in filas[0][2] and filas[0][2].split().count("redes_OCI") == 1
    assert len({f[3] for f in filas}) == 3 and {f[4] for f in filas} == {nombre_mazo(paquete)}


@pytest.mark.parametrize("frente", ["=1+1", "+SUM(A1:A2)", "-1+2", "@cmd", "\t=cmd", "\r\n+cmd", "\ufeff=cmd"])
def test_formula_neutralizada_sin_cambiar_texto_visible(frente):
    salida = campo_html(frente)
    assert salida.startswith("<span>") and salida.endswith("</span>")
    assert salida[0] not in "=+-@"


def test_paquete_reproducible_ids_notas_campos_y_sin_media(paquete, tmp_path):
    datos = exportar_apkg(paquete)
    assert exportar_apkg(paquete) == datos
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        assert z.namelist() == ["collection.anki2", "media"] and json.loads(z.read("media")) == {}
        ruta = tmp_path / "paquete.anki2"
        ruta.write_bytes(z.read("collection.anki2"))
    with sqlite3.connect(ruta) as db:
        notas = db.execute("SELECT guid,mid,flds,tags FROM notes ORDER BY id").fetchall()
        modelos, decks = db.execute("SELECT models,decks FROM col").fetchone()
    assert len(notas) == len({n[0] for n in notas}) == 3
    assert notas[0][0] == guid(paquete.generation_id, "primera")
    assert notas[0][2].split("\x1f") == [
        campo_html(paquete.contenido_adaptado.items[0].frente),
        campo_html(paquete.contenido_adaptado.items[0].dorso),
    ]
    assert etiquetas(paquete.contenido_adaptado.items[0].etiquetas) == ["redes_OCI", "ação", "nm_-etiqueta", "_formula"]
    assert {m["name"] for m in json.loads(modelos).values()} == {"NuevaMente " + paquete.generation_id}
    assert nombre_mazo(paquete) in {d["name"] for d in json.loads(decks).values()}


def _importar(col, ruta, formato):
    if formato == "apkg":
        return col.import_anki_package(ImportAnkiPackageRequest(package_path=ruta.as_posix()))
    metadata = col.get_csv_metadata(ruta.as_posix(), None)
    assert metadata.is_html and metadata.tags_column == 3 and metadata.guid_column == 4 and metadata.deck_column == 5
    assert list(metadata.global_notetype.field_columns) == [1, 2]
    return col.import_csv(ImportCsvRequest(path=ruta.as_posix(), metadata=metadata))


@pytest.mark.parametrize("formato", ["csv", "tsv", "apkg"])
def test_importacion_anki_real_sin_ajustes_y_segundo_import_no_duplica(paquete, coleccion, tmp_path, formato):
    ruta = tmp_path / ("tarjetas." + formato)
    ruta.write_bytes(exportar_apkg(paquete) if formato == "apkg" else exportar_texto(paquete, formato))
    _importar(coleccion, ruta, formato)
    assert coleccion.note_count() == coleccion.card_count() == 3
    _importar(coleccion, ruta, formato)
    assert coleccion.note_count() == coleccion.card_count() == 3
    notas = [coleccion.get_note(nid) for nid in coleccion.find_notes("")]
    assert {n.guid for n in notas} == {guid(paquete.generation_id, t.id) for t in paquete.contenido_adaptado.items}
    assert any("ação" in n.tags for n in notas)
    assert all("<script>" not in n.fields[1] and "[sound:" not in n.fields[1] for n in notas)
    assert coleccion.decks.by_name(nombre_mazo(paquete)) is not None


def test_importar_csv_tsv_apkg_del_mismo_mazo_no_duplica(paquete, coleccion, tmp_path):
    for formato in ("apkg", "csv", "tsv", "apkg"):
        ruta = tmp_path / ("tarjetas." + formato)
        ruta.write_bytes(exportar_apkg(paquete) if formato == "apkg" else exportar_texto(paquete, formato))
        _importar(coleccion, ruta, formato)
        assert coleccion.note_count() == 3


def test_generaciones_distintas_no_colisionan_ni_comparten_modelo(paquete, coleccion, tmp_path):
    for gid in ("gen_uno", "gen_dos"):
        datos = paquete.model_dump(mode="json")
        datos["generation_id"] = gid
        otro = PedagogicalOutput.model_validate(datos)
        ruta = tmp_path / (gid + ".apkg")
        ruta.write_bytes(exportar_apkg(otro))
        _importar(coleccion, ruta, "apkg")
    assert coleccion.note_count() == 6 and len({n[0] for n in coleccion.db.all("SELECT DISTINCT mid FROM notes")}) == 2


def test_cache_anki_no_reescribe_ni_cambia_entre_descargas(espacio_exportaciones):
    e = espacio_exportaciones
    paquete = e.publicar("gen_anki", FLASHCARDS)
    servicio = ServicioExportaciones(e.catalogo)
    for formato in ("csv", "tsv", "apkg"):
        primera = servicio.descargar(e.token, paquete.generation_id, formato)
        assert primera == servicio.descargar(e.token, paquete.generation_id, formato)
        assert e.storage.get(clave_export("ws_estudio", paquete.generation_id, formato)) == primera.contenido


def test_exportar_anki_multidocumento(paquete):
    from app.exports.anki import exportar_texto
    from app.schemas.pedagogical import Referencia
    from app.schemas.responses import DocumentoFuente

    p = paquete.model_copy(deep=True)
    f1 = DocumentoFuente(document_id="doc1", titulo="Doc 1", version="v1", hash="hash1")
    f2 = DocumentoFuente(document_id="doc2", titulo="Doc 2", version="v2", hash="hash2")
    p.documento_fuente = f1
    p.documentos_fuente = [f1, f2]
    p.contenido_adaptado.items[0].referencias = [
        Referencia(document_id="doc1", chunk_id="chk1", pagina=1),
        Referencia(document_id="doc2", chunk_id="chk2", pagina=2),
    ]
    csv_bytes = exportar_texto(p, "csv").decode("utf-8")
    assert "doc_doc1" in csv_bytes
    assert "doc_doc2" in csv_bytes
