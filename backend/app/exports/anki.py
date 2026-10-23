"""Anki #45: identidad estable, texto literal y paquetes reproducibles sin media."""

import csv
import hashlib
import html
import io
import itertools
import json
import re
import sqlite3
import unicodedata
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import genanki

from app.exports.generation_mode import etiqueta_generacion
from app.schemas.pedagogical import FlashcardDeck


# con esta funcion genero un identificador numerico determinista de 48 bits a partir del hash sha256 compatible con las especificaciones de anki
def _id(generation_id, tipo):
    # 48 bits: portables a JSON/JS y muy por debajo de int64 de Anki.
    return (1 << 40) + int(hashlib.sha256(f"nm-anki-v1:{tipo}:{generation_id}".encode()).hexdigest()[:12], 16)


# aqui genero un identificador unico universal estable para cada tarjeta evitando tarjetas duplicadas al importar el mazo en anki
def guid(generation_id, card_id):
    return "nm" + genanki.guid_for("nm-anki-v1", generation_id, card_id)


def nombre_mazo(paquete):
    return "NuevaMente::" + paquete.generation_id


# con esta funcion escapo el texto y lo convierto a html seguro desactivando comandos especiales de anki como sonidos o formulas latex
def campo_html(valor):
    limpio = valor.replace("\r\n", "\n").replace("\r", "\n")
    limpio = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", limpio)
    # El wrapper evita fórmulas incluso tras trim de una planilla. Los
    # corchetes se codifican para desactivar [sound:] y [latex] de Anki.
    escapado = html.escape(limpio, quote=True).replace("[", "&#91;").replace("]", "&#93;")
    return "<span>" + escapado.replace("\n", "<br>").replace("\t", "&#9;") + "</span>"


# aqui normalizo las etiquetas del mazo convirtiendo caracteres no alfanumericos en guiones bajos bajo la norma unicode nfc
def etiquetas(valores):
    resultado = []
    for valor in valores:
        tag = re.sub(r"[^\w:.-]", "_", unicodedata.normalize("NFC", valor).strip())
        if tag.startswith("-"):
            tag = "nm_" + tag
        if tag and tag not in resultado:
            resultado.append(tag)
    return resultado


def _mazo(paquete):
    if not isinstance(paquete.contenido_adaptado, FlashcardDeck):
        raise TypeError("Anki requiere un mazo de flashcards aprobado")
    return paquete.contenido_adaptado


def _frente(paquete, valor):
    etiqueta = etiqueta_generacion(paquete)
    return (campo_html(etiqueta) + "<br><br>" if etiqueta else "") + campo_html(valor)


# con esta funcion exporto las flashcards en formato delimitado por comas (csv) o tabulaciones (tsv) compatible con importaciones directas
def exportar_texto(paquete, formato):
    mazo = _mazo(paquete)
    if formato not in ("csv", "tsv"):
        raise ValueError("Separador no admitido")
    separador = "," if formato == "csv" else "\t"
    salida = io.StringIO(newline="")
    salida.write("#separator:" + ("Comma" if formato == "csv" else "Tab") + "\n")
    salida.write("#html:true\n#tags column:3\n#guid column:4\n#deck column:5\n")
    salida.write("#columns:" + separador.join(("Front", "Back", "Tags", "GUID", "Deck")) + "\n")
    writer = csv.writer(salida, delimiter=separador, quoting=csv.QUOTE_ALL, lineterminator="\n")
    for tarjeta in mazo.items:
        tags_tarjeta = list(tarjeta.etiquetas)
        for r in getattr(tarjeta, "referencias", []):
            if getattr(r, "document_id", None):
                tags_tarjeta.append(f"doc_{r.document_id}")
        writer.writerow(
            (
                _frente(paquete, tarjeta.frente),
                campo_html(tarjeta.dorso),
                " ".join(etiquetas(tags_tarjeta)),
                guid(paquete.generation_id, tarjeta.id),
                nombre_mazo(paquete),
            )
        )
    return salida.getvalue().encode("utf-8")


# aqui construyo el paquete binario de anki (archivo apkg) empaquetando la base de datos sqlite interna dentro de un archivo zip comprimido segun el issue 45
def exportar_apkg(paquete):
    mazo = _mazo(paquete)
    modelo = genanki.Model(
        _id(paquete.generation_id, "modelo"),
        "NuevaMente " + paquete.generation_id,
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[{"name": "NuevaMente", "qfmt": "{{Front}}", "afmt": '{{FrontSide}}<hr id="answer">{{Back}}'}],
        css=".card { font-family: sans-serif; font-size: 20px; text-align: left; padding: 20px; }",
    )
    deck = genanki.Deck(_id(paquete.generation_id, "mazo"), nombre_mazo(paquete))
    for tarjeta in mazo.items:
        tags_tarjeta = list(tarjeta.etiquetas)
        for r in getattr(tarjeta, "referencias", []):
            if getattr(r, "document_id", None):
                tags_tarjeta.append(f"doc_{r.document_id}")
        deck.add_note(
            genanki.Note(
                model=modelo,
                fields=[_frente(paquete, tarjeta.frente), campo_html(tarjeta.dorso)],
                tags=etiquetas(tags_tarjeta),
                guid=guid(paquete.generation_id, tarjeta.id),
            )
        )
    salida = io.BytesIO()
    # genanki.write_to_file deja un mkstemp externo y timestamps de ZIP
    # variables. Usamos su escritura de colección con recursos propios.
    with TemporaryDirectory(prefix="nm-anki-") as directorio:
        ruta = Path(directorio) / "collection.anki2"
        db = sqlite3.connect(ruta)
        try:
            timestamp = paquete.created_at.timestamp()
            genanki.Package(deck).write_to_db(db.cursor(), timestamp, itertools.count(int(timestamp * 1000)))
            db.commit()
        finally:
            db.close()
        with zipfile.ZipFile(salida, "w", compression=zipfile.ZIP_STORED) as archivo:
            for nombre, contenido in (("collection.anki2", ruta.read_bytes()), ("media", json.dumps({}).encode())):
                info = zipfile.ZipInfo(nombre, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 0
                info.external_attr = 0o600 << 16
                archivo.writestr(info, contenido)
    return salida.getvalue()
