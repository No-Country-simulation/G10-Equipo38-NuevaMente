"""Generador del documento demo `redes_vcn_oci.pdf` (issue #05).

Por qué un script y no un PDF hecho a mano en Word/Overleaf: el PDF es un
INSUMO del proyecto (la fuente común de los tres escenarios de demo, §18.1),
y regenerarlo tiene que ser reproducible y auditable. Este script usa
ReportLab (decisión del issue: "Generado con ReportLab/LaTeX para control
total del contenido") y produce 10-12 páginas A4 con:

- Los conceptos que los escenarios A/B/C enseñan:
  VCN y CIDR, subredes públicas/privadas,
  gateways (Internet, NAT, Service), security lists vs NSG y tablas de
  ruteo.
- Un DIAGRAMA DE ARQUITECTURA dibujado con primitivas vectoriales
  (rectángulos, flechas, texto) en vez de una imagen bitmap: al ser
  vectores, rasteriza nítido a cualquier zoom — requisito del diferencial
  multimodal (§18.2: "descripción del diagrama + cita a la página
  original").
- Una sección DELIBERADAMENTE INSUFICIENTE (§9 "Costos y límites"): dos
  frases vagas, sin cifras ni respaldo. NO es un descuido: es el caso
  negativo de prueba que pide el issue, para que la demo muestre el bloqueo
  por calidad (paso 10 del guion: "Rechazo intencional"). No lleva ninguna
  marca visible en el PDF: el pipeline debe detectarlo solo, como
  detectaría un documento real mal escrito. Este comentario es el único
  registro del artificio, junto con el issue.

Cómo regenerarlo (desde la raíz del repo):
    pip install -r requirements-dev.txt   # incluye reportlab, versión fijada
    python documents/generar_pdf_vcn.py

Verificación de aceptación (extracción con PyPDF):
    python -c "from pypdf import PdfReader; r = PdfReader('documents/redes_vcn_oci.pdf'); print(len(r.pages), r.pages[3].extract_text()[:120])"
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

SALIDA = Path(__file__).resolve().parent / "redes_vcn_oci.pdf"

# Paleta sobria y legible (el documento se lee en pantalla y se imprime).
AZUL = colors.HexColor("#1B3A5C")
AZUL_CLARO = colors.HexColor("#E8F0F7")
VERDE = colors.HexColor("#2E7D52")
VERDE_CLARO = colors.HexColor("#E9F5EE")
NARANJA = colors.HexColor("#C0662A")
NARANJA_CLARO = colors.HexColor("#FBF0E7")
GRIS = colors.HexColor("#5A6472")
GRIS_CLARO = colors.HexColor("#F2F4F6")


def estilos() -> dict[str, ParagraphStyle]:
    """Estilos tipográficos del documento (títulos, cuerpo, código, notas)."""
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle(
            "titulo", parent=base["Title"], fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=AZUL
        ),
        "subtitulo": ParagraphStyle(
            "subtitulo",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=12,
            leading=16,
            textColor=GRIS,
            alignment=1,
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=19,
            textColor=AZUL,
            spaceBefore=14,
            spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=GRIS,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "cuerpo": ParagraphStyle(
            "cuerpo", parent=base["BodyText"], fontName="Helvetica", fontSize=10, leading=14, spaceAfter=6
        ),
        "nota": ParagraphStyle(
            "nota",
            parent=base["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=9,
            leading=12,
            textColor=GRIS,
            spaceAfter=6,
        ),
    }


def tabla(data: list[list[str]], anchos: list[float] | None = None) -> Table:
    """Tabla estándar del documento: encabezado azul, celdas claras, grillas finas."""
    t = Table(data, colWidths=anchos)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), AZUL),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRIS_CLARO]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D2DB")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return t


class DiagramaVCN(Flowable):
    """El diagrama de arquitectura, dibujado con primitivas vectoriales.

    Por qué heredar de Flowable: Platypus (el motor de maquetación de
    ReportLab) posiciona "piezas" de contenido; al definir nuestra pieza
    propia con un método draw(), el diagrama se ubica como cualquier
    párrafo o tabla y se dibuja con las primitivas de bajo nivel del canvas
    (rect, line, string). Todo queda vectorial dentro del PDF.
    """

    # El frame útil de SimpleDocTemplate (A4 menos márgenes de 1 pulgada) mide
    # ~15,9 cm; el diagrama se declara con ese ancho y las coordenadas internas
    # (diseñadas sobre una grilla de 16,5 cm) se escalan para no desbordar la
    # página: así nada queda cortado en el borde.
    def __init__(self, ancho: float = 15.8 * cm, alto: float = 18.2 * cm) -> None:
        super().__init__()
        self.width, self.height = ancho, alto

    # Helpers de dibujo: cada uno encapsula una figura con texto centrado.

    def _caja(
        self,
        canv,
        x: float,
        y: float,
        w: float,
        h: float,
        texto: str,
        relleno: colors.Color,
        borde: colors.Color,
        tamaño: float = 8.5,
        texto_color: colors.Color = colors.black,
        lineas: list[str] | None = None,
    ) -> None:
        canv.setFillColor(relleno)
        canv.setStrokeColor(borde)
        canv.setLineWidth(1.2)
        canv.roundRect(x, y, w, h, 4, stroke=1, fill=1)
        canv.setFillColor(texto_color)
        canv.setFont("Helvetica-Bold", tamaño)
        # drawCentredString centra el texto en x (punto medio de la caja).
        # OJO: NO soporta "\n" (lo dibuja como glifo roto); para varias líneas
        # se usa la lista `lineas`, dibujada manualmente y centrada en bloque.
        if lineas:
            interlineado = tamaño * 1.25
            y_primera = y + h / 2 + (len(lineas) - 1) * interlineado / 2 - tamaño * 0.35
            for indice, linea in enumerate(lineas):
                canv.drawCentredString(x + w / 2, y_primera - indice * interlineado, linea)
        else:
            canv.drawCentredString(x + w / 2, y + h / 2 - tamaño * 0.35, texto)

    def _flecha(
        self, canv, x1: float, y1: float, x2: float, y2: float, color: colors.Color = GRIS, dashed: bool = False
    ) -> None:
        canv.setStrokeColor(color)
        canv.setFillColor(color)
        canv.setLineWidth(1.4)
        if dashed:
            canv.setDash(4, 3)
        canv.line(x1, y1, x2, y2)
        # Punta de flecha: triángulo pequeño orientado hacia (x2, y2).
        import math

        angulo = math.atan2(y2 - y1, x2 - x1)
        largo, ancho_p = 6, 4
        punta = [
            (x2, y2),
            (
                x2 - largo * math.cos(angulo) + ancho_p * math.sin(angulo),
                y2 - largo * math.sin(angulo) - ancho_p * math.cos(angulo),
            ),
            (
                x2 - largo * math.cos(angulo) - ancho_p * math.sin(angulo),
                y2 - largo * math.sin(angulo) + ancho_p * math.cos(angulo),
            ),
        ]
        p = canv.beginPath()
        p.moveTo(*punta[0])
        p.lineTo(*punta[1])
        p.lineTo(*punta[2])
        p.close()
        canv.drawPath(p, stroke=0, fill=1)
        canv.setDash()

    def _rotulo(self, canv, x: float, y: float, texto: str, tamaño: float = 7.5, color: colors.Color = GRIS) -> None:
        canv.setFillColor(color)
        canv.setFont("Helvetica", tamaño)
        canv.drawCentredString(x, y, texto)

    def draw(self) -> None:
        """Dibuja la topología completa (coordenadas desde el origen inferior izquierdo).

        Las coordenadas se diseñaron sobre una grilla de 16,5 cm de ancho y se
        escalan al ancho real del flowable: nada queda cortado por el margen.
        Entre las dos subredes se deja un PASILLO vertical (x=8.25) por donde
        sube la flecha punteada del NAT sin cruzar ninguna caja.
        """
        canv = self.canv
        factor = self.width / (16.5 * cm)
        canv.scale(factor, factor)

        # ---- Capa exterior: Internet arriba, servicios de OCI abajo ----
        self._caja(canv, 7.1 * cm, 17.6 * cm, 5 * cm, 1.2 * cm, "INTERNET", GRIS_CLARO, GRIS, 10)
        self._caja(
            canv,
            4.4 * cm,
            0.3 * cm,
            7.7 * cm,
            1.0 * cm,
            "Servicios de OCI (p. ej. Object Storage)",
            AZUL_CLARO,
            AZUL,
            8.5,
        )

        # ---- Gateways sobre el borde superior e inferior de la VCN ----
        self._caja(canv, 7.0 * cm, 15.3 * cm, 5.2 * cm, 1.0 * cm, "Internet Gateway", NARANJA_CLARO, NARANJA)
        self._caja(canv, 1.2 * cm, 15.3 * cm, 3.2 * cm, 1.0 * cm, "NAT Gateway", NARANJA_CLARO, NARANJA)
        self._caja(canv, 12.9 * cm, 1.9 * cm, 3.2 * cm, 1.0 * cm, "Service Gateway", NARANJA_CLARO, NARANJA)

        # ---- El contenedor VCN (región) ----
        canv.setStrokeColor(AZUL)
        canv.setLineWidth(2)
        canv.setFillColor(colors.white)
        canv.roundRect(0.6 * cm, 3.0 * cm, 15.3 * cm, 11.9 * cm, 8, stroke=1, fill=0)
        canv.setFillColor(AZUL)
        canv.setFont("Helvetica-Bold", 11)
        canv.drawCentredString(
            8.25 * cm, 14.35 * cm, "VCN  10.0.0.0/16  (región, todos los dominios de disponibilidad)"
        )

        # ---- Subred pública (izquierda) ----
        self._caja(canv, 1.4 * cm, 8.2 * cm, 6.2 * cm, 5.4 * cm, "", VERDE_CLARO, VERDE)
        canv.setFillColor(VERDE)
        canv.setFont("Helvetica-Bold", 9.5)
        canv.drawCentredString(4.5 * cm, 12.9 * cm, "Subred pública")
        canv.drawCentredString(4.5 * cm, 12.35 * cm, "10.0.1.0/24")
        self._caja(canv, 2.0 * cm, 10.4 * cm, 5.0 * cm, 1.2 * cm, "Balanceador / Servidor web", colors.white, VERDE)
        self._caja(canv, 2.0 * cm, 8.7 * cm, 5.0 * cm, 1.2 * cm, "Host bastión (SSH temporal)", colors.white, VERDE)
        self._rotulo(canv, 4.5 * cm, 7.85 * cm, "Security List: 80/443 desde 0.0.0.0/0; SSH restringido", 6.5, VERDE)

        # ---- Subred privada (derecha) ----
        self._caja(canv, 8.9 * cm, 4.0 * cm, 6.2 * cm, 9.6 * cm, "", AZUL_CLARO, AZUL)
        canv.setFillColor(AZUL)
        canv.setFont("Helvetica-Bold", 9.5)
        canv.drawCentredString(12.0 * cm, 12.9 * cm, "Subred privada")
        canv.drawCentredString(12.0 * cm, 12.35 * cm, "10.0.2.0/24  ·  sin IP pública")
        self._caja(canv, 9.4 * cm, 10.0 * cm, 5.2 * cm, 1.2 * cm, "Servidor de aplicaciones", colors.white, AZUL)
        self._caja(canv, 9.4 * cm, 7.8 * cm, 5.2 * cm, 1.2 * cm, "Base de datos (puerto 1521)", colors.white, AZUL)
        self._caja(canv, 9.4 * cm, 5.6 * cm, 5.2 * cm, 1.2 * cm, "Cache (puerto 6379)", colors.white, AZUL)
        self._rotulo(canv, 12.0 * cm, 4.5 * cm, "NSG por rol: solo tráfico entre niveles", 6.5, AZUL)

        # ---- Tablas de ruteo (abajo, dentro de la VCN, sin tocar subredes) ----
        # Tres líneas dibujadas manualmente: drawCentredString no soporta "\n".
        self._caja(
            canv,
            1.4 * cm,
            4.2 * cm,
            3.2 * cm,
            1.6 * cm,
            "",
            NARANJA_CLARO,
            NARANJA,
            7.5,
            lineas=["Tabla de ruteo", "(subred pública)", "0.0.0.0/0 -> IGW"],
        )
        self._caja(
            canv,
            4.9 * cm,
            4.2 * cm,
            3.2 * cm,
            1.6 * cm,
            "",
            NARANJA_CLARO,
            NARANJA,
            7.5,
            lineas=["Tabla de ruteo", "(subred privada)", "0.0.0.0/0 -> NAT"],
        )

        # ---- Flujo 1: Internet -> IGW -> subred pública -> privada ----
        self._flecha(canv, 9.6 * cm, 17.6 * cm, 9.6 * cm, 16.3 * cm, GRIS)
        self._flecha(canv, 8.2 * cm, 15.3 * cm, 5.2 * cm, 11.6 * cm, VERDE)
        self._rotulo(canv, 5.9 * cm, 13.9 * cm, "HTTPS entrante", 6.5, VERDE)
        # Flecha web -> aplicaciones SIN rótulo: el pasillo central (x=8.25)
        # lo cruza cualquier texto ubicado ahí; el flujo se explica en §6.
        self._flecha(canv, 7.0 * cm, 11.0 * cm, 9.4 * cm, 10.6 * cm, AZUL)

        # ---- Flujo 2: subred privada -> (pasillo central) -> NAT (salida) ----
        # Codo en tres segmentos sin punta + remate con flecha hacia el NAT,
        # para no cruzar ninguna caja.
        for x1, y1, x2, y2 in [
            (8.9 * cm, 6.2 * cm, 8.25 * cm, 6.2 * cm),
            (8.25 * cm, 6.2 * cm, 8.25 * cm, 15.0 * cm),
            (8.25 * cm, 15.0 * cm, 2.8 * cm, 15.0 * cm),
        ]:
            canv.setStrokeColor(NARANJA)
            canv.setFillColor(NARANJA)
            canv.setLineWidth(1.3)
            canv.setDash(4, 3)
            canv.line(x1, y1, x2, y2)
            canv.setDash()
        self._flecha(canv, 2.8 * cm, 15.0 * cm, 2.8 * cm, 15.3 * cm, NARANJA)
        self._rotulo(canv, 6.3 * cm, 15.12 * cm, "salida vía NAT (sin IP pública)", 6.2, NARANJA)

        # ---- Flujo 3: privado -> Service Gateway -> servicios OCI ----
        self._flecha(canv, 13.8 * cm, 4.0 * cm, 14.5 * cm, 2.9 * cm, AZUL)
        self._rotulo(canv, 11.8 * cm, 3.5 * cm, "a servicios OCI sin Internet", 6, AZUL)
        self._flecha(canv, 12.9 * cm, 2.4 * cm, 11.5 * cm, 1.1 * cm, GRIS)

        # ---- Leyenda ----
        leyenda_y = 1.55 * cm
        canv.setFont("Helvetica", 7)
        canv.setFillColor(GRIS)
        canv.drawString(0.6 * cm, leyenda_y, "Leyenda:")
        for indice, (color, texto) in enumerate(
            [
                (VERDE, "subred pública (ruta a IGW)"),
                (AZUL, "subred privada (sin IP pública)"),
                (NARANJA, "gateways y tablas de ruteo"),
            ]
        ):
            origen = 2.0 * cm + indice * 5.0 * cm
            canv.setFillColor(color)
            canv.rect(origen, leyenda_y - 0.1 * cm, 0.28 * cm, 0.28 * cm, stroke=0, fill=1)
            canv.setFillColor(GRIS)
            canv.drawString(origen + 0.4 * cm, leyenda_y - 0.05 * cm, texto)


def construir() -> None:
    """Ensambla el PDF completo: portada, secciones de texto, tablas y diagrama."""
    s = estilos()
    doc = SimpleDocTemplate(
        str(SALIDA),
        pagesize=A4,
        title="Redes VCN en Oracle Cloud Infrastructure — Guía de fundamentos",
        author="Equipo 38 — NuevaMente (documento demo, contenido público/simulado)",
        subject="Fuente común de los escenarios de demo (issue #05)",
    )

    historia: list[Flowable] = [
        Paragraph("Redes VCN en Oracle Cloud Infrastructure", s["titulo"]),
        Paragraph("Guía de fundamentos con diagrama de arquitectura", s["subtitulo"]),
        Spacer(1, 6),
        Paragraph(
            "Documento público/simulado para fines educativos. Versión v1. "
            "Fuente común de los tres escenarios de demostración del proyecto NuevaMente.",
            s["nota"],
        ),
        # ----------------------------------------------------------------- 1
        Paragraph("1. Introducción y propósito", s["h1"]),
        Paragraph(
            "Una <b>Virtual Cloud Network (VCN)</b> es la red privada que aísla y organiza los "
            "recursos de cómputo dentro de Oracle Cloud Infrastructure (OCI). Toda máquina virtual, "
            "balanceador o base de datos que se crea en OCI vive dentro de alguna VCN: definir bien "
            "la red es decidir quién puede hablar con quién, qué puede salir a Internet y qué queda "
            "completamente aislado. Este documento explica los fundamentos con un ejemplo concreto "
            "de referencia que se retoma en todas las secciones: una aplicación web de dos niveles "
            "(servidores web públicos y servidores de aplicaciones privados) desplegada en una sola región.",
            s["cuerpo"],
        ),
        Paragraph(
            "La guía está pensada para tres audiencias distintas: quien se inicia en cloud "
            "(conceptos con analogías), quien desarrolla e integra (puertos, rutas y reglas "
            "operativas) y quien decide (implicaciones de aislamiento y costo a alto nivel).",
            s["cuerpo"],
        ),
        # ----------------------------------------------------------------- 2
        Paragraph("2. Conceptos fundamentales: VCN y direccionamiento CIDR", s["h1"]),
        Paragraph(
            "La VCN es un contenedor <b>regional</b>: abarca todos los dominios de disponibilidad de "
            "la región elegida, de modo que un fallo de un datacenter no aísla la red. Su extensión "
            "se define con un bloque <b>CIDR</b> (Classless Inter-Domain Routing): una notación "
            "'dirección/máscara' que indica cuántas direcciones IP privadas contiene. Las VCN usan "
            "rangos privados definidos por el estándar RFC 1918, que no enrutan en Internet pública.",
            s["cuerpo"],
        ),
        tabla(
            [
                ["Bloque CIDR", "Direcciones disponibles", "Uso típico"],
                ["10.0.0.0/16", "65.536", "VCN completa de un ambiente (el ejemplo de esta guía)"],
                ["10.0.1.0/24", "256", "Subred pública de balanceadores y servidores web"],
                ["10.0.2.0/24", "256", "Subred privada de aplicaciones y bases de datos"],
                ["172.16.0.0/16", "65.536", "Alternativa frecuente cuando 10.x ya está en uso"],
            ],
            anchos=[3.6 * cm, 4.2 * cm, 8.6 * cm],
        ),
        Spacer(1, 4),
        Paragraph(
            "Cómo se lee un CIDR en la práctica: la máscara indica cuántos bits de la dirección "
            "están fijos. En 10.0.2.0/24 los primeros 24 bits (los tres primeros números) son "
            "fijos y el último varía: la subred contiene las direcciones 10.0.2.0 a 10.0.2.255, de "
            "las cuales la primera identifica la red y la última es el broadcast de difusión; las "
            "254 restantes se asignan a interfaces. En 10.0.0.0/16 solo los dos primeros números "
            "están fijos, y por eso caben 256 subredes /24 como las del ejemplo.",
            s["cuerpo"],
        ),
        Paragraph(
            "Regla práctica: planificar el CIDR de la VCN con holgura desde el inicio. Cambiar el "
            "bloque de una VCN en producción exige recrear la red y recablear sus recursos; ampliar "
            "subredes dentro de un /16 es mucho más barato que empezar por un /24 ajustado.",
            s["cuerpo"],
        ),
        # ----------------------------------------------------------------- 3
        Paragraph("3. Subredes públicas y privadas", s["h1"]),
        Paragraph(
            "Una <b>subred</b> es una porción del CIDR de la VCN. Puede pertenecer a un dominio de "
            "disponibilidad o cubrir toda la región. Lo que decide si una subred es pública o "
            "privada NO es una casilla de configuración: es su <b>tabla de ruteo</b>. Si la tabla "
            "envía el tráfico 0.0.0.0/0 (todo Internet) hacia un Internet Gateway, la subred es "
            "pública; si no existe esa ruta, ninguna de sus instancias es alcanzable desde afuera.",
            s["cuerpo"],
        ),
        tabla(
            [
                ["Aspecto", "Subred pública (10.0.1.0/24)", "Subred privada (10.0.2.0/24)"],
                ["Ruta por defecto", "0.0.0.0/0 -> Internet Gateway", "0.0.0.0/0 -> NAT Gateway"],
                ["IP públicas", "Opcional por instancia", "No aplica: sin ruta directa"],
                ["Qué aloja", "Balanceadores, servidores web, bastión", "Aplicaciones, bases de datos, cachés"],
                ["Riesgo de exposición", "Debe filtrarse con reglas (sección 6)", "Mínimo: solo niveles internos"],
                ["Actualizaciones salientes", "Directas", "Vía NAT o Service Gateway"],
            ],
            anchos=[3.8 * cm, 6.4 * cm, 6.2 * cm],
        ),
        Spacer(1, 4),
        Paragraph(
            "Sobre los dominios de disponibilidad: una región de OCI contiene uno o más dominios "
            "(datacenters) aislados entre sí. Como la VCN es regional, la misma estructura de "
            "subredes y reglas protege los recursos sin importar en qué dominio caigan, y es "
            "posible repartir instancias del mismo nivel entre dominios para tolerar fallas de un "
            "datacenter completo. Las subredes pueden ser específicas de un dominio o regionales; "
            "para niveles con balanceador, las regionales simplifican el reparto.",
            s["cuerpo"],
        ),
        # ----------------------------------------------------------------- 4
        PageBreak(),
        Paragraph("4. Diagrama de arquitectura de referencia", s["h1"]),
        Paragraph(
            "El siguiente diagrama resume la topología usada en todo el documento. Es la página "
            "que la demo multimodal cita como fuente original: los tres escenarios (flashcards, "
            "tutorial y resumen ejecutivo) derivan preguntas y pasos de esta misma figura.",
            s["cuerpo"],
        ),
        DiagramaVCN(),
        PageBreak(),
        # ----------------------------------------------------------------- 5
        Paragraph("5. Gateways: las puertas de la VCN", s["h1"]),
        Paragraph(
            "Un <b>gateway</b> es el punto por donde el tráfico cruza el borde de la VCN. Cada tipo "
            "resuelve una necesidad distinta; elegir mal el gateway es la causa más común de "
            "'servidor que no sale a Internet' o 'base de datos expuesta sin querer'.",
            s["cuerpo"],
        ),
        tabla(
            [
                ["Gateway", "Dirección del tráfico", "Para qué se usa", "En el ejemplo"],
                [
                    "Internet Gateway (IGW)",
                    "Entrada y salida",
                    "Habilitar IPs públicas y tráfico entrante HTTPS",
                    "Sirve la subred pública",
                ],
                [
                    "NAT Gateway",
                    "Solo salida",
                    "Instancias privadas que actualizan o llaman APIs externas sin IP pública",
                    "Salida de la subred privada",
                ],
                [
                    "Service Gateway",
                    "Solo salida",
                    "Alcanzar servicios de OCI (p. ej. Object Storage) sin pasar por Internet",
                    "Backups hacia Object Storage",
                ],
                [
                    "Dynamic Routing Gateway (DRG)",
                    "Entrada y salida",
                    "Conectar la VCN con redes on-premise u otras VCN por VPN/FastConnect",
                    "No usado en el ejemplo",
                ],
            ],
            anchos=[3.7 * cm, 2.6 * cm, 6.6 * cm, 4.0 * cm],
        ),
        Spacer(1, 4),
        Paragraph(
            "Diferencia clave entre NAT y Service Gateway: el NAT da salida a Internet genérica "
            "(la instancia inicia la conexión; nadie puede iniciar contra ella), mientras que el "
            "Service Gateway alcanza servicios de Oracle por la red interna, con menor exposición "
            "y mejor trayecto. Para hablar con Object Storage desde la base de datos privada, la "
            "ruta correcta es el Service Gateway, no el NAT.",
            s["cuerpo"],
        ),
        # ----------------------------------------------------------------- 6
        Paragraph("6. Recorrido de un paquete: de la solicitud HTTP a la respuesta", s["h1"]),
        Paragraph(
            "Para consolidar los conceptos, sigamos una solicitud real contra la topología de "
            "referencia. Una usuaria abre <i>https://app.ejemplo.com/inventario</i> desde su "
            "navegador:",
            s["cuerpo"],
        ),
        Paragraph(
            "<b>Paso 1 — DNS y borde.</b> El nombre resuelve a la IP pública del balanceador en la "
            "subred pública. El paquete cruza Internet y entra a la VCN por el Internet Gateway: "
            "sin IGW, la IP pública simplemente no es alcanzable.<br/>"
            "<b>Paso 2 — Reglas de entrada.</b> La Security List de la subred pública acepta TCP "
            "443 desde 0.0.0.0/0 (HTTPS de cualquier origen). Si el puerto fuera 22, la misma "
            "subred lo rechazaría salvo que la regla limiting provenga de las IPs operativas."
            "<br/>"
            "<b>Paso 3 — Distribución.</b> El balanceador elige un servidor web sano y reenvía la "
            "petición por la red interna (10.0.0.0/16 es ruta Local: no toca ningún gateway)."
            "<br/>"
            "<b>Paso 4 — Nivel de aplicación.</b> El servidor web llama al 8080 de un servidor de "
            "aplicaciones de la subred privada. El NSG de aplicaciones acepta la conexión solo "
            "porque proviene del NSG de servidores web: cualquier otro origen se descarta."
            "<br/>"
            "<b>Paso 5 — Datos.</b> La aplicación consulta la base de datos por TCP 1521. La "
            "conexión sale por la IP privada; la tabla de ruteo de la subred privada coincide con "
            "10.0.0.0/16 Local y el paquete permanece en la VCN."
            "<br/>"
            "<b>Paso 6 — Respuesta.</b> Las reglas son stateful: cada nivel responde por la "
            "conexión ya aceptada, sin abrir puertos adicionales de vuelta. La respuesta escala "
            "hasta el navegador de la usuaria.",
            s["cuerpo"],
        ),
        Paragraph(
            "El mismo circuito en sentido inverso no existe para Internet hacia la base de datos: "
            "no hay ruta entrante ni regla que lo permita. Esa asimetría deliberada es el corazón "
            "del diseño.",
            s["cuerpo"],
        ),
        # ----------------------------------------------------------------- 7
        Paragraph("7. Security Lists y Network Security Groups", s["h1"]),
        Paragraph(
            "Ambos son firewalls de capa 4 (IP y puerto) que actúan sobre el tráfico de la subred "
            "o de la instancia. <b>Security Lists (SL)</b> se aplican a nivel de subred y todas las "
            "instancias de la subred las comparten. <b>Network Security Groups (NSG)</b> se aplican "
            "a nivel de interfaz de red (vNIC), lo que permite tratar a cada servidor como miembro "
            "de un grupo con reglas propias. En la práctica se combinan: SL para reglas amplias de "
            "la subred y NSG para el detalle por rol.",
            s["cuerpo"],
        ),
        tabla(
            [
                ["Regla de ejemplo", "Dirección", "Origen/Destino", "Puerto", "Efecto"],
                ["SL subred pública", "Entrada", "0.0.0.0/0", "TCP 80 y 443", "Cualquiera puede navegar el sitio"],
                [
                    "SL subred pública",
                    "Entrada",
                    "IP del equipo operativo",
                    "TCP 22",
                    "SSH solo desde la oficina/laboratorio",
                ],
                [
                    "NSG de aplicaciones",
                    "Entrada",
                    "NSG de servidores web",
                    "TCP 8080",
                    "Solo el nivel web llama a las apps",
                ],
                [
                    "NSG de base de datos",
                    "Entrada",
                    "NSG de aplicaciones",
                    "TCP 1521",
                    "Solo las apps conectan a la base",
                ],
                [
                    "NSG de base de datos",
                    "Salida",
                    "NSG de aplicaciones",
                    "TCP 1521",
                    "Respuestas stateful a conexiones aceptadas",
                ],
            ],
            anchos=[3.6 * cm, 2.0 * cm, 4.2 * cm, 2.2 * cm, 4.9 * cm],
        ),
        Spacer(1, 4),
        Paragraph(
            "Las reglas de esta tabla son <b>stateful</b> (con estado): si se acepta una conexión "
            "entrante, su respuesta saliente se permite automáticamente. Existen reglas stateless "
            "para casos especiales (p. ej. balanceadores que inspeccionan tráfico), que exigen "
            "autorizar explícitamente el tráfico de retorno en ambos sentidos.",
            s["cuerpo"],
        ),
        # ----------------------------------------------------------------- 8
        Paragraph("8. Tablas de ruteo", s["h1"]),
        Paragraph(
            "La <b>tabla de ruteo</b> decide el siguiente salto de cada paquete según su destino. "
            "Se asocia a subredes (una subred usa exactamente una tabla; una tabla puede servir a "
            "varias). Cuando varios prefijos coinciden, gana el más específico (la ruta por "
            "defecto 0.0.0.0/0 es la menos específica de todas).",
            s["cuerpo"],
        ),
        Paragraph("Tabla de ruteo de la subred pública (10.0.1.0/24):", s["h2"]),
        tabla(
            [
                ["Destino", "Tipo de destino", "Propósito"],
                ["10.0.0.0/16", "Local", "Tráfico interno de la VCN nunca sale de ella"],
                ["0.0.0.0/0", "Internet Gateway", "Todo lo demás sale a Internet (subred pública)"],
            ],
            anchos=[3.4 * cm, 4.4 * cm, 8.6 * cm],
        ),
        Paragraph("Tabla de ruteo de la subred privada (10.0.2.0/24):", s["h2"]),
        tabla(
            [
                ["Destino", "Tipo de destino", "Propósito"],
                ["10.0.0.0/16", "Local", "Comunicación entre niveles dentro de la VCN"],
                ["0.0.0.0/0", "NAT Gateway", "Salida iniciada por las instancias, sin exposición"],
                ["Rango de servicios de OCI", "Service Gateway", "Backups y lecturas de Object Storage internamente"],
            ],
            anchos=[4.4 * cm, 3.8 * cm, 8.2 * cm],
        ),
        Spacer(1, 4),
        Paragraph(
            "Ejercicio de lectura del diagrama (sección 4): siga la flecha punteada desde la base "
            "de datos. Pasa por el NAT Gateway para actualizar el sistema operativo y por el "
            "Service Gateway para escribir backups. En ningún momento acepta conexiones iniciadas "
            "desde Internet: la ruta entrante no existe en su tabla.",
            s["cuerpo"],
        ),
        # ----------------------------------------------------------------- 9
        Paragraph("9. Matriz de conectividad de referencia", s["h1"]),
        Paragraph(
            "El diagrama (sección 4) muestra la topología; esta matriz la cierra declarando qué "
            "conexiones existen y cuáles están prohibidas. Un diagrama sin matriz deja lecturas "
            "libres; la matriz las vuelve verificables.",
            s["cuerpo"],
        ),
        tabla(
            [
                ["Origen", "Destino", "Puerto", "¿Permitido?", "Medio que lo habilita"],
                ["Internet", "Balanceador web", "TCP 443", "Sí", "IGW + SL pública"],
                ["Internet", "Base de datos", "TCP 1521", "No", "Sin ruta ni regla: por diseño"],
                ["Servidor web", "Aplicaciones", "TCP 8080", "Sí", "Ruta Local + NSG de apps"],
                ["Aplicaciones", "Base de datos", "TCP 1521", "Sí", "Ruta Local + NSG de base"],
                ["Base de datos", "Internet (actualizaciones)", "TCP 443", "Sí (salida)", "NAT Gateway"],
                ["Base de datos", "Object Storage", "TCP 443", "Sí (salida)", "Service Gateway"],
                ["Aplicaciones", "Servidor web", "TCP 22", "No", "Sin regla NSG: separación de niveles"],
            ],
            anchos=[3.6 * cm, 4.0 * cm, 1.8 * cm, 2.2 * cm, 4.8 * cm],
        ),
        # ----------------------------------------------------------------- 10
        Paragraph("10. Creación de la topología, paso a paso", s["h1"]),
        Paragraph(
            "Orden operativo para reproducir el ejemplo desde cero. El orden importa: los "
            "gateways y las tablas de ruteo deben existir antes de lanzar instancias, o habrá "
            "ventanas de inaccesibilidad.",
            s["cuerpo"],
        ),
        Paragraph(
            "1. Crear la VCN con el CIDR 10.0.0.0/16 y un nombre por función y ambiente.<br/>"
            "2. Crear el Internet Gateway y el NAT Gateway en la región; crear el Service Gateway "
            "apuntando a los servicios de la región.<br/>"
            "3. Crear la tabla de ruteo pública (0.0.0.0/0 -> IGW) y la privada (0.0.0.0/0 -> "
            "NAT; ruta a servicios -> Service Gateway).<br/>"
            "4. Crear la subred pública 10.0.1.0/24 asociada a la tabla pública y la privada "
            "10.0.2.0/24 asociada a la tabla privada.<br/>"
            "5. Cargar la Security List de la subred pública con 443/80 desde 0.0.0.0/0 y SSH "
            "restringido a las IPs operativas.<br/>"
            "6. Crear los NSG por rol (web, aplicaciones, base de datos) con las reglas de la "
            "sección 7.<br/>"
            "7. Lanzar el balanceador y los servidores web en la subred pública, asignando el NSG "
            "correspondiente a cada interfaz.<br/>"
            "8. Lanzar aplicaciones, base de datos y caché en la subred privada con sus NSG."
            "<br/>"
            "9. Verificar la matriz de la sección 9 con pruebas de conectividad desde cada nivel "
            "(las prohibidas deben fallar).<br/>"
            "10. Documentar cualquier desviación antes de dar el ambiente por listo.",
            s["cuerpo"],
        ),
        # ----------------------------------------------------------------- 11
        Paragraph("11. Errores frecuentes y diagnóstico", s["h1"]),
        tabla(
            [
                ["Síntoma", "Causa habitual", "Primer chequeo"],
                [
                    "'La instancia no sale a Internet'",
                    "Subred sin ruta 0.0.0.0/0 a IGW/NAT",
                    "Tabla de ruteo asociada a la subred",
                ],
                [
                    "'El sitio no carga desde afuera'",
                    "Falta el Internet Gateway o la IP pública",
                    "Existencia del IGW y IP asignada",
                ],
                ["'SSH con timeout'", "Puerto 22 no autorizado para mi IP", "Security List de la subred pública"],
                [
                    "'La app no conecta a la base'",
                    "NSG sin regla entre niveles",
                    "NSG de base: origen = NSG de apps, 1521",
                ],
                [
                    "'El backup a Object Storage falla'",
                    "Ruta a servicios por NAT en vez de Service Gateway",
                    "Ruta del rango de servicios en la tabla privada",
                ],
                [
                    "'Reglas que se pisan entre equipos'",
                    "Todo en Security Lists compartidas",
                    "Migrar el detalle a NSG por rol",
                ],
            ],
            anchos=[4.6 * cm, 6.2 * cm, 5.6 * cm],
        ),
        # ----------------------------------------------------------------- 12
        Paragraph("12. Buenas prácticas de diseño", s["h1"]),
        Paragraph(
            "1. Separar niveles: web en subred pública, datos en subredes privadas sin ruta "
            "entrante.<br/>2. Nombrar subredes y tablas por función y ambiente (prod-web-a) para "
            "que el diagnóstico humano sea posible.<br/>3. Restringir SSH/RDP a IPs conocidas, jamás "
            "dejar 0.0.0.0/0 en puertos de administración.<br/>4. Prefiere NSG por rol cuando varios "
            "equipos comparten una subred: las reglas dejan de pelearse.<br/>5. Documentar la matriz "
            "'quién habla con quién' junto al diagrama; el diagrama sin la matriz es media verdad."
            "<br/>6. Reservar bloques por ambiente (10.0 producción, 10.1 staging) dentro del /16 "
            "para no re-planificar después.",
            s["cuerpo"],
        ),
        # ----------------------------------------------------------------- 13
        Paragraph("13. Escalado y evolución de la topología", s["h1"]),
        Paragraph(
            "La topología de referencia es un punto de partida, no un destino. Cuando la "
            "organización agrega ambientes o conecta redes existentes, la estructura crece por "
            "extensiones previstas en lugar de rediseños.",
            s["cuerpo"],
        ),
        tabla(
            [
                ["Necesidad nueva", "Extensión prevista", "Qué NO hacer"],
                [
                    "Separar producción de pruebas",
                    "Subredes propias por ambiente dentro del mismo /16",
                    "Compartir subred y 'filtrar con cuidado'",
                ],
                [
                    "Conectar dos VCNs entre sí",
                    "Peering local o a través de un DRG central",
                    "Rutas solapadas con CIDRs repetidos",
                ],
                [
                    "Conectar la oficina (on-premise)",
                    "VPN o FastConnect terminando en un DRG",
                    "Exponer servicios por IP públicas 'temporalmente'",
                ],
                [
                    "Presencia en otra región",
                    "VCN nueva con CIDR distinto + peering remoto",
                    "Reutilizar el mismo bloque 10.0.0.0/16",
                ],
                [
                    "Más niveles (p. ej. mensajería)",
                    "Subred privada adicional y NSG por rol",
                    "Mezclar niveles en la subred de base de datos",
                ],
            ],
            anchos=[4.4 * cm, 6.6 * cm, 5.4 * cm],
        ),
        Spacer(1, 4),
        Paragraph(
            "La regla que atraviesa todas las filas: los bloques CIDR de las redes que se van a "
            "conectar deben ser disjuntos desde el día uno. Conectar redes con rangos solapados "
            "obliga a renumerar en producción, que es exactamente el costo que la planificación "
            "inicial (sección 2) buscaba evitar. El patrón hub-and-spoke con un DRG central "
            "mantiene el crecimiento gobernable: cada VCN nueva habla con el hub y no directamente "
            "con sus pares.",
            s["cuerpo"],
        ),
        # ----------------------------------------------------------------- 14
        Paragraph("14. Preguntas de autoevaluación", s["h1"]),
        Paragraph(
            "Cada pregunta remite a la sección que la responde. Si puede responderlas sin volver "
            "a leer, la base de los tres escenarios está consolidada.",
            s["cuerpo"],
        ),
        tabla(
            [
                ["#", "Pregunta", "Respuesta breve", "Sección"],
                [
                    "1",
                    "¿Qué hace pública a una subred?",
                    "Su tabla de ruteo envía 0.0.0.0/0 a un Internet Gateway",
                    "3 y 8",
                ],
                [
                    "2",
                    "¿Puede la base de datos aceptar conexiones desde Internet?",
                    "No: no tiene ruta entrante ni regla que lo permita",
                    "6 y 9",
                ],
                [
                    "3",
                    "¿Para qué sirve el NAT si ya existe un Internet Gateway?",
                    "Salida a Internet de instancias sin IP pública ni exposición",
                    "5",
                ],
                [
                    "4",
                    "¿Por qué escribir backups con Service Gateway y no con NAT?",
                    "Alcanza servicios de OCI por la red interna, sin pasar por Internet",
                    "5",
                ],
                [
                    "5",
                    "¿Qué diferencia a un NSG de una Security List?",
                    "El NSG se aplica por interfaz (rol); la SL por subred completa",
                    "7",
                ],
                [
                    "6",
                    "¿Qué significa que una regla sea stateful?",
                    "La respuesta de una conexión aceptada se permite automáticamente",
                    "7",
                ],
            ],
            anchos=[0.8 * cm, 6.2 * cm, 7.2 * cm, 1.8 * cm],
        ),
        # ----------------------------------------------------------------- 14
        # SECCIÓN DELIBERADAMENTE INSUFICIENTE (ver comentario del encabezado
        # del script): caso negativo para el bloqueo por calidad de la demo.
        Paragraph("15. Costos y límites", s["h1"]),
        Paragraph(
            "En general, montar una VCN es bastante accesible y los costos suelen venir de otros "
            "recursos. Los límites dependen del tipo de cuenta y conviene revisarlos de vez en "
            "cuando.",
            s["cuerpo"],
        ),
        # ----------------------------------------------------------------- 15
        Paragraph("16. Glosario", s["h1"]),
        tabla(
            [
                ["Término", "Significado"],
                ["VCN", "Virtual Cloud Network: red privada regional que aísla recursos en OCI"],
                ["CIDR", "Notación dirección/máscara que define el tamaño de un bloque de IPs"],
                ["Dominio de disponibilidad", "Datacenter independiente dentro de una región"],
                ["IGW / NAT / Service Gateway", "Puertas de la VCN para Internet, salida privada y servicios OCI"],
                ["SL / NSG", "Firewalls por subred y por interfaz de red, respectivamente"],
                ["Tabla de ruteo", "Mapa destino -> siguiente salto asociado a una subred"],
            ],
            anchos=[5.4 * cm, 11.0 * cm],
        ),
    ]

    doc.build(historia)
    print(f"PDF generado: {SALIDA}")


if __name__ == "__main__":
    construir()
