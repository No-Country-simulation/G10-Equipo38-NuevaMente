"""PDF didáctico desde el canónico aprobado, con fuentes locales y sin I/O remoto."""

import html
import math
import re
import threading
import unicodedata
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
)

from app.exports.generation_mode import etiqueta_generacion
from app.exports.markdown import _ETIQUETAS, _IDIOMAS
from app.schemas.pedagogical import (
    ExecutiveSummary,
    FlashcardDeck,
    InteractiveQuiz,
    PracticalTutorial,
    VideoLessonScript,
)
from app.schemas.responses import PedagogicalOutput

_LOCK = threading.RLock()
_FONTS = Path(__file__).with_name("fonts")
_ANCHO = A4[0] - 40 * mm
_ALTO = A4[1] - 44 * mm
_ACENTO = colors.HexColor("#5e6ad2")
_TINTA = colors.HexColor("#23252a")
_BORDE = colors.HexColor("#d9dbe4")
_SUAVE = colors.HexColor("#f5f5fa")
_FORMATOS = {
    "es": ("Flashcards", "Quiz", "Tutorial", "Resumen ejecutivo", "Guion de clase", "continuación", "líneas"),
    "en": ("Flashcards", "Quiz", "Tutorial", "Executive summary", "Lesson script", "continued", "lines"),
    "pt": ("Cartões", "Quiz", "Tutorial", "Resumo executivo", "Roteiro de aula", "continuação", "linhas"),
}
_TIPOS = ("flashcards", "quiz", "tutorial", "resumen_ejecutivo", "guion_clase")


# con esta funcion normalizo el texto eliminando caracteres de control no imprimibles para evitar fallos de renderizado en el pdf
def _limpiar(valor):
    valor = unicodedata.normalize("NFC", str(valor).replace("\r\n", "\n").replace("\r", "\n"))
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", valor)


# aqui escapo el texto para que pueda ser interpretado de forma segura por el motor de parrafos (paragraph) de reportlab
def _texto(valor):
    # El XML de Paragraph lo genera exclusivamente la plantilla. Ni imágenes,
    # links ni instrucciones de ReportLab procedentes del documento se interpretan.
    return html.escape(_limpiar(valor), quote=True).replace("\n", "<br/>")


# con esta funcion registro las fuentes truetype locales en reportlab para soportar caracteres internacionales de forma determinista y sin dependencias externas
def _registrar_fuentes():
    for nombre, archivo in (
        ("NM", "Vera.ttf"),
        ("NM-Bold", "VeraBd.ttf"),
        ("NM-Italic", "VeraIt.ttf"),
        ("NM-BoldItalic", "VeraBI.ttf"),
        ("NM-Code", "JetBrainsMono-Regular.ttf"),
    ):
        if nombre not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(nombre, str(_FONTS / archivo)))
    pdfmetrics.registerFontFamily("NM", normal="NM", bold="NM-Bold", italic="NM-Italic", boldItalic="NM-BoldItalic")


# con este componente visual personalizado de reportlab dibujo bloques de codigo con numeracion de lineas fondo coloreado y soporte de division entre paginas
class _Codigo(Flowable):
    """Conserva líneas e indentación; divide sólo por líneas, con numeración y continuación."""

    def __init__(self, filas, etiqueta, continuado=False):
        super().__init__()
        self.filas, self.etiqueta, self.continuado = filas, etiqueta, continuado
        self.width, self.height = _ANCHO, 36 + len(filas) * 12

    def wrap(self, ancho, alto):
        return self.width, self.height

    def split(self, ancho, alto):
        # Un bloque corto pasa entero a la página siguiente. Uno mayor que una
        # página mantiene tamaño legible y se parte sin recortar líneas.
        if self.height <= _ALTO:
            return []
        n = math.floor((alto - 36) / 12)
        if n < 2:
            return []
        return [_Codigo(self.filas[:n], self.etiqueta, self.continuado), _Codigo(self.filas[n:], self.etiqueta, True)]

    def draw(self):
        c = self.canv
        c.setFillColor(_SUAVE)
        c.setStrokeColor(_BORDE)
        c.roundRect(0, 0, self.width, self.height, 4, fill=1, stroke=1)
        c.setFillColor(_ACENTO)
        c.setFont("NM-Bold", 8)
        c.drawString(12, self.height - 16, self.etiqueta + (" (+)" if self.continuado else ""))
        for i, (numero, linea) in enumerate(self.filas):
            y = self.height - 32 - i * 12
            c.setFillColor(colors.HexColor("#62666d"))
            c.setFont("NM-Code", 8.5)
            c.drawRightString(35, y, str(numero) if numero is not None else "+")
            c.setFillColor(_TINTA)
            c.drawString(45, y, linea)


# aqui divido las lineas de codigo respetando el ancho de la columna e insertando indicadores de continuacion visual sin modificar el codigo fuente
def _filas_codigo(codigo):
    ancho_caracter = pdfmetrics.stringWidth("M", "NM-Code", 8.5)
    longitud = int((_ANCHO - 57) / ancho_caracter)
    filas = []
    for numero, linea in enumerate(_limpiar(codigo).expandtabs(4).split("\n"), 1):
        # Corte visual marcado con '+': no elimina espacios ni cambia el código.
        trozos = [linea[i : i + longitud] for i in range(0, len(linea), longitud)] or [""]
        filas.extend((numero if i == 0 else None, trozo) for i, trozo in enumerate(trozos))
    return filas


# con esta clase armo la estructura documental del pdf integrando portada objetivos tarjetas cuestionarios con soluciones y citas bibliograficas
class _PlantillaPDF:
    def __init__(self, paquete):
        self.paquete = paquete
        idioma = paquete.metadatos.idioma_salida.value
        self.l, self.nombres = _ETIQUETAS[idioma], _FORMATOS[idioma]
        self.nombre_formato = self.nombres[_TIPOS.index(paquete.contenido_adaptado.tipo)]
        self.story, self.referencias = [], {}
        self.estilos = {
            "body": ParagraphStyle("body", fontName="NM", fontSize=10.5, leading=15.5, textColor=_TINTA, spaceAfter=5),
            "title": ParagraphStyle(
                "title", fontName="NM-Bold", fontSize=23, leading=29, textColor=_TINTA, spaceAfter=16
            ),
            "h1": ParagraphStyle(
                "h1",
                fontName="NM-Bold",
                fontSize=14,
                leading=18,
                textColor=_ACENTO,
                spaceBefore=10,
                spaceAfter=6,
                keepWithNext=True,
            ),
            "h2": ParagraphStyle(
                "h2",
                fontName="NM-Bold",
                fontSize=11,
                leading=16,
                textColor=_TINTA,
                spaceBefore=10,
                spaceAfter=5,
                keepWithNext=True,
            ),
            "small": ParagraphStyle(
                "small", fontName="NM", fontSize=8.5, leading=12, textColor=colors.HexColor("#62666d"), spaceAfter=4
            ),
            "label": ParagraphStyle(
                "label", fontName="NM-Bold", fontSize=8, leading=11, textColor=_ACENTO, spaceAfter=5
            ),
        }
        for r in paquete.referencias:
            self._numero(r)

    def p(self, valor, estilo="body", *, marcado=False):
        return Paragraph(valor if marcado else _texto(valor), self.estilos[estilo])

    def agregar(self, valor, estilo="body"):
        self.story.append(self.p(valor, estilo))

    def titulo(self, clave, sufijo="", estilo="h1"):
        self.agregar(self.l[clave] + sufijo, estilo)

    def campo(self, clave, valor, *, italica=False):
        texto = _texto(valor)
        if italica:
            texto = "<i>" + texto + "</i>"
        self.story.append(self.p("<b>" + self.l[clave] + ":</b> " + texto, marcado=True))

    def lista(self, clave, valores):
        self.titulo(clave)
        self.bullets(valores)

    def bullets(self, valores):
        for valor in valores:
            self.story.append(self.p("&#8226; " + _texto(valor), marcado=True))
        if not valores:
            self.agregar(self.l["ninguno"])

    def _numero(self, r):
        doc_id = getattr(r, "document_id", None)
        clave = (doc_id, r.chunk_id, r.pagina, r.seccion)
        if clave not in self.referencias:
            self.referencias[clave] = len(self.referencias) + 1
        return self.referencias[clave]

    def citas(self, referencias):
        numeros = dict.fromkeys(self._numero(r) for r in referencias)
        return self.p(
            " ".join(f'<link href="#ref_{n}" color="#5e6ad2">[{n}]</link>' for n in numeros), "small", marcado=True
        )

    def tabla(self, encabezados, columnas):
        tabla = Table(
            [[self.p(e, "label") for e in encabezados], columnas],
            colWidths=[_ANCHO / len(columnas)] * len(columnas),
            repeatRows=1,
            splitInRow=1,
            style=[
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), _SUAVE),
                ("BOX", (0, 0), (-1, -1), 0.5, _BORDE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, _BORDE),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ],
        )
        self.story.extend([tabla, Spacer(1, 9)])

    def portada(self):
        m, contenido = self.paquete.metadatos, self.paquete.contenido_adaptado
        self.agregar(self.nombre_formato.upper(), "label")
        self.agregar(contenido.titulo, "title")
        if etiqueta := etiqueta_generacion(self.paquete):
            self.agregar(etiqueta, "label")
        self.campo("audiencia", self.l[m.perfil_aplicado.value])
        self.campo("idioma", _IDIOMAS[m.idioma_salida.value])
        self.campo("tiempo", f"{m.tiempo_estimado_estudio_minutos} {self.l['minutos']}")
        self.lista("objetivos", m.objetivos_aprendizaje)
        previos = m.prerrequisitos + (contenido.prerrequisitos if isinstance(contenido, PracticalTutorial) else [])
        self.lista("prerrequisitos", list(dict.fromkeys(previos)))
        self.lista("conceptos", m.conceptos_clave)
        if introduccion := getattr(contenido, "introduccion_contextualizada", None):
            self.titulo("introduccion")
            self.agregar(introduccion)

    def cuerpo(self):
        contenido = self.paquete.contenido_adaptado
        if isinstance(contenido, FlashcardDeck):
            self.titulo("tarjetas")
            for i, tarjeta in enumerate(contenido.items, 1):
                inicio = len(self.story)
                self.titulo("tarjeta", f" {i} · {tarjeta.id}", "h2")
                self.tabla(
                    [self.l["frente"], self.l["dorso"]],
                    [[self.p(tarjeta.frente)], [self.p(tarjeta.dorso), self.citas(tarjeta.referencias)]],
                )
                if tarjeta.pista_didactica:
                    self.campo("pista", tarjeta.pista_didactica)
                if tarjeta.etiquetas:
                    self.campo("etiquetas", ", ".join(tarjeta.etiquetas))
                self.story[inicio:] = [KeepTogether(self.story[inicio:])]
        elif isinstance(contenido, InteractiveQuiz):
            self.titulo("preguntas")
            for i, pregunta in enumerate(contenido.preguntas, 1):
                self.titulo("pregunta", f" {i}", "h2")
                self.agregar(pregunta.enunciado)
                for opcion in pregunta.opciones:
                    self.agregar(opcion.option_id + ". " + opcion.texto)
                self.story.append(self.citas(pregunta.referencias))
        elif isinstance(contenido, PracticalTutorial):
            self.campo("audiencia", contenido.audiencia)
            self.titulo("pasos")
            for i, paso in enumerate(contenido.pasos, 1):
                self.titulo("paso", f" {i}", "h2")
                self.agregar(paso.instruccion)
                if paso.codigo:
                    self.story.extend([_Codigo(_filas_codigo(paso.codigo), self.l["codigo"]), Spacer(1, 9)])
                self.campo("resultado", paso.resultado_esperado)
                self.campo("verificacion", paso.verificacion)
                self.story.append(self.citas(paso.referencias))
        elif isinstance(contenido, ExecutiveSummary):
            self.lista("puntos", contenido.puntos_clave)
            self.tabla(
                [self.l["impacto"], self.l["implicaciones"]],
                [
                    [self.p(contenido.impacto_cualitativo)],
                    [self.p("&#8226; " + _texto(v), marcado=True) for v in contenido.implicaciones],
                ],
            )
            self.lista("acciones", contenido.acciones)
            self.story.append(self.citas(contenido.referencias))
        elif isinstance(contenido, VideoLessonScript):
            self.lista("objetivos_clase", contenido.objetivos)
            self.titulo("escenas")
            for i, escena in enumerate(contenido.escenas, 1):
                self.titulo("escena", f" {i} · {escena.duracion_min:g} {self.l['minutos']}", "h2")
                self.campo("narracion", escena.narracion, italica=True)
                self.titulo("diapositiva", estilo="h2")
                self.bullets(escena.puntos_diapositiva)
                if escena.pregunta_interactiva:
                    self.campo("interaccion", escena.pregunta_interactiva)
                self.story.append(self.citas(escena.referencias))
        else:
            raise TypeError("Formato pedagógico no soportado")

    def fuentes(self):
        inicio = len(self.story)
        fuentes = getattr(self.paquete, "documentos_fuente", None) or [self.paquete.documento_fuente]
        self.story.append(Spacer(1, 10))
        if len(fuentes) > 1:
            self.titulo("fuentes", estilo="h2")
            for f in fuentes:
                self.story.append(
                    self.p(
                        "<b>"
                        + self.l["fuente"]
                        + ":</b> "
                        + _texto(f.titulo)
                        + " · <b>"
                        + self.l["version"]
                        + ":</b> "
                        + _texto(f.version),
                        "small",
                        marcado=True,
                    )
                )
                self.agregar("SHA-256: " + f.hash.removeprefix("sha256:"), "small")
                if f.procedencia:
                    self.story.append(
                        self.p("<b>" + self.l["procedencia"] + ":</b> " + _texto(f.procedencia), "small", marcado=True)
                    )
        else:
            fuente = fuentes[0]
            self.story.append(
                self.p(
                    "<b>"
                    + self.l["fuente"]
                    + ":</b> "
                    + _texto(fuente.titulo)
                    + " · <b>"
                    + self.l["version"]
                    + ":</b> "
                    + _texto(fuente.version),
                    "small",
                    marcado=True,
                )
            )
            self.agregar("SHA-256: " + fuente.hash.removeprefix("sha256:"), "small")
            if fuente.procedencia:
                self.story.append(
                    self.p("<b>" + self.l["procedencia"] + ":</b> " + _texto(fuente.procedencia), "small", marcado=True)
                )
        self.titulo("referencias", estilo="h2")
        for (doc_id, cid, pagina, seccion), n in self.referencias.items():
            detalle = f"[{n}] "
            if doc_id:
                detalle += f"{self.l.get('documento', 'Documento')}: {_texto(doc_id)} · "
            detalle += f"{self.l['fragmento']}: " + _texto(cid)
            if pagina is not None:
                detalle += f" · {self.l['pagina']}: {pagina}"
            if seccion is not None:
                detalle += " · " + self.l["seccion"] + ": " + _texto(seccion)
            self.story.append(self.p(f'<a name="ref_{n}"/>' + detalle, "small", marcado=True))
        self.story[inicio:] = [KeepTogether(self.story[inicio:])]

    def soluciones(self):
        contenido = self.paquete.contenido_adaptado
        if isinstance(contenido, InteractiveQuiz):
            self.story.append(PageBreak())
            self.titulo("soluciones")
            for i, pregunta in enumerate(contenido.preguntas, 1):
                self.titulo("pregunta", f" {i}", "h2")
                correcta = next(o for o in pregunta.opciones if o.option_id == pregunta.correct_option_id)
                self.campo("correcta", correcta.option_id + ": " + correcta.texto)
                self.campo("justificacion", pregunta.justificacion)
                self.story.append(self.citas(pregunta.referencias))

    def pagina(self, canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(_BORDE)
        canvas.line(20 * mm, A4[1] - 18 * mm, A4[0] - 20 * mm, A4[1] - 18 * mm)
        canvas.setFillColor(_ACENTO)
        canvas.setFont("NM-Bold", 8)
        canvas.drawString(20 * mm, A4[1] - 14 * mm, "NuevaMente")
        canvas.setFillColor(colors.HexColor("#62666d"))
        canvas.setFont("NM", 8)
        canvas.drawRightString(A4[0] - 20 * mm, A4[1] - 14 * mm, self.nombre_formato)
        canvas.drawRightString(A4[0] - 20 * mm, 13 * mm, f"{self.l['pagina']} {doc.page}")
        canvas.restoreState()


# aqui compilo el documento pdf utilizando un cerrojo de concurrencia y opciones de reporte estables para generar bytes reproducibles segun el issue 44
def renderizar_pdf(paquete: PedagogicalOutput) -> bytes:
    # Las fuentes dinámicas de ReportLab comparten registro global. Serializar
    # registro/build evita estados cruzados entre descargas de distintos espacios.
    with _LOCK:
        _registrar_fuentes()
        plantilla = _PlantillaPDF(paquete)
        plantilla.portada()
        plantilla.cuerpo()
        plantilla.fuentes()
        plantilla.soluciones()
        salida = BytesIO()
        doc = BaseDocTemplate(
            salida,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=22 * mm,
            bottomMargin=22 * mm,
            title=_limpiar(paquete.contenido_adaptado.titulo),
            author="NuevaMente",
            invariant=1,
            pageCompression=1,
            lang=paquete.metadatos.idioma_salida.value,
        )
        frame = Frame(20 * mm, 22 * mm, _ANCHO, _ALTO, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        doc.addPageTemplates(PageTemplate(id="didactico", frames=frame, onPage=plantilla.pagina))
        # Además de texto/estructura estables, invariant garantiza bytes estables:
        # la caché existente compara la vista derivada con su canónico aprobado.
        doc.build(plantilla.story)
        return salida.getvalue()
