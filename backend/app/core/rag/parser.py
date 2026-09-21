"""Parser y validación de documentos (issue #11, referencia §4.1 y §11.3).

Qué hace este archivo: es la PUERTA DE ENTRADA de todo documento al sistema.
Convierte un archivo (PDF, Markdown o TXT) o un texto pegado en un
`ResultadoParseo`: texto por páginas, secciones con línea de origen, hash
del original, estimación de tokens y un informe de COBERTURA (qué se pudo
procesar y qué no). Todo lo que viene después (chunker #12, embeddings #13,
vectorstore #17) consume esta salida.

Reglas del plan que este módulo hace cumplir:

1. NADA SE FÍA DEL NOMBRE NI DEL MIME (§11.3): "se validan extensión,
   tamaño, firma/formato real y parseo. El MIME declarado por el cliente no
   es prueba suficiente". Un archivo .pdf que no empieza con la firma %PDF
   se rechaza; un .txt con bytes binarios también. El nombre subido se usa
   SOLO como etiqueta (titulo), jamás para construir rutas.
2. LÍMITES ANTES DE PROCESAR (§4.1): 20 MB para PDF, 5 MB para MD/TXT, 100
   páginas, 100.000 tokens extraídos y 20 páginas visuales. Todos
   configurables en `LimitesIngesta` (los tests usan límites chicos).
3. NUNCA TRUNCAR EN SILENCIO (§4.1): "Excederlos no habilita truncamiento
   silencioso". Si algo se pasa del límite, se RECHAZA con código estable y
   mensaje accionable; jamás se procesa "la mitad" del documento.
4. ESCANEADOS → CONTRATO VISUAL (§4.2, issue #30): una página sin texto
   extraíble no es un error: es una página VISUAL (escaneo o diagrama). El
   parser la lista en `paginas_visuales`, marca `requiere_vision=True` y NO
   declara el documento ready: "antes de conectar visión no declarar ready
   un documento incompleto". Cuando el issue #30 exista, esas páginas se
   rasterizan y se interpretan; hoy quedan explícitamente pendientes.
5. PRESUPUESTO DE EXTRACCIÓN (§11.3): la extracción corre con un presupuesto
   de tiempo (por defecto 30 s) revisado página a página; si se agota, se
   rechaza con TIMEOUT_EXTRACCION en vez de colgar al usuario.

Sobre la estimación de TOKENS: el límite de §4.1 es "100.000 tokens
extraídos", pero el tokenizador real vive con el chunker (#12). Acá usamos
una estimación conservadora (máximo entre caracteres/4 y palabras×1.3,
redondeado hacia arriba): si la estimación conservadora ya excede el
límite, el documento seguro lo excede. #12 podrá reemplazar el estimador
sin cambiar el contrato de `ResultadoParseo`.

Códigos de error: `ParserErrorCode` son códigos DE MÁQUINA estables de este
módulo. La capa de API (#19) los mapea a los HTTP del contrato (413 para
los límites de tamaño, 422 para formato/contenido) usando la tabla de
docs/contratos-api.md; cambiar un valor de este enum rompe a #19, así que
solo se agrega, nunca se renombra.
"""

from __future__ import annotations

import hashlib
import io
import math
import multiprocessing
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.schemas.enums import DocumentStatus

# --------------------------------------------------------------------------
# Límites operativos (§4.1) y parámetros del parser
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LimitesIngesta:
    """Límites configurables de la ingesta. Valores por defecto = §4.1.

    `frozen=True` hace la instancia inmutable: los tests construyen la
    suya con `dataclasses.replace` y nadie puede "relajar" un límite a
    mitad de la corrida por accidente.
    """

    max_bytes_pdf: int = 20 * 1024 * 1024  # 20 MB
    max_bytes_texto: int = 5 * 1024 * 1024  # 5 MB (MD y TXT)
    max_paginas: int = 100
    max_tokens_extraidos: int = 100_000
    max_paginas_visuales: int = 20
    # Presupuesto de reloj para la extracción completa (§11.3: "timeout").
    presupuesto_extraccion_segundos: float = 30.0
    max_memoria_pdf_bytes: int = 512 * 1024 * 1024
    # Umbral heurístico: página con menos caracteres que esto cuenta como
    # página VISUAL (diagrama/escaneo), no como texto extraído.
    minimo_caracteres_por_pagina: int = 20
    # Un documento cuyo texto total quede por debajo de esto (sin ser un
    # escaneo puro) se considera de extracción insuficiente (§4.1).
    minimo_tokens_documento: int = 50


# --------------------------------------------------------------------------
# Errores con código estable
# --------------------------------------------------------------------------


class ParserErrorCode(str, Enum):
    """Códigos estables de rechazo de ingesta (ver docstring del módulo)."""

    FORMATO_NO_SOPORTADO = "FORMATO_NO_SOPORTADO"
    DEMASIADO_GRANDE = "DEMASIADO_GRANDE"
    LIMITE_PAGINAS = "LIMITE_PAGINAS"
    LIMITE_TOKENS = "LIMITE_TOKENS"
    LIMITE_PAGINAS_VISUALES = "LIMITE_PAGINAS_VISUALES"
    CORRUPTO = "CORRUPTO"
    CIFRADO = "CIFRADO"
    EXTRACCION_INSUFICIENTE = "EXTRACCION_INSUFICIENTE"
    BINARIO = "BINARIO"
    TIMEOUT_EXTRACCION = "TIMEOUT_EXTRACCION"


class ParserError(Exception):
    """Rechazo accionable: código estable + qué hacer al respecto.

    `detalles` lleva los números que el usuario necesita para corregir
    (límite, valor recibido, página problemática...). La API (#19) los
    pondrá en error.details del envoltorio estándar.
    """

    def __init__(self, codigo: ParserErrorCode, mensaje: str, detalles: dict | None = None) -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje
        self.detalles = detalles or {}


# --------------------------------------------------------------------------
# Resultado del parseo
# --------------------------------------------------------------------------


@dataclass
class PaginaTexto:
    """Texto extraído de UNA página PDF, con su número (base 1)."""

    numero: int
    texto: str


@dataclass
class SeccionTexto:
    """Una sección del documento con su ubicación de origen.

    Para MD la sección es un encabezado (#, ##...) con su rango de líneas;
    para TXT se reporta el contenido completo como una única sección.
    El chunker (#12) usará estos metadatos para que cada chunk conserve
    de dónde salió (trazabilidad de las citas, §4.3).
    """

    titulo: str
    linea_inicio: int
    linea_fin: int
    texto: str


@dataclass
class ResultadoParseo:
    """Salida del parser: TODO lo que el pipeline aguas abajo necesita.

    - `hash_sha256`: huella del original (bytes del archivo / del texto
      pegado). Es el hash de documento_fuente del contrato (#03) y la clave
      para reutilizar indexaciones (§4.5).
    - `estado_documento`: ready cuando el texto extraído es completo;
      processing cuando hay páginas visuales pendientes de #30. El caller
      (#19) lo persiste con store.registrar_documento/actualizar_documento.
    - `cobertura`: informe en lenguaje humano de qué se procesó y qué no
      ("La ingestión informa qué páginas y secciones pudo procesar", §4.1).
    """

    extension: str  # "pdf" | "md" | "txt"
    titulo_etiqueta: str  # nombre subido: SOLO etiqueta, nunca ruta (§11.3)
    hash_sha256: str
    paginas: list[PaginaTexto] = field(default_factory=list)
    secciones: list[SeccionTexto] = field(default_factory=list)
    cantidad_tokens: int = 0
    # Páginas sin texto extraíble (diagramas/escaneos) → contrato visual #30.
    paginas_visuales: list[int] = field(default_factory=list)
    requiere_vision: bool = False
    estado_documento: DocumentStatus = DocumentStatus.READY
    detalle_estado: str | None = None
    cobertura: str = ""


# --------------------------------------------------------------------------
# Helpers internos
# --------------------------------------------------------------------------


def estimar_tokens(texto: str) -> int:
    """Estimación conservadora de tokens (ver docstring del módulo).

    Dos heurísticas y nos quedamos con la MÁXIMA: caracteres/4 (regla usual
    para español/inglés) y palabras×1.3 (textos con muchas palabras cortas).
    Conservadora = si esta cifra entra en el límite, el conteo real también.
    """
    if not texto:
        return 0
    por_caracteres = math.ceil(len(texto) / 4)
    por_palabras = math.ceil(len(texto.split()) * 1.3)
    return max(por_caracteres, por_palabras)


def _firma_pdf(contenido: bytes) -> bool:
    """True si el contenido tiene la firma %PDF- al inicio.

    Se tolera que aparezca dentro del primer KB (algunos generadores
    anteponean basura), pero un .pdf sin firma en ninguna parte no es un
    PDF: es un archivo mal etiquetado y se rechaza (§11.3).
    """
    return b"%PDF-" in contenido[:1024]


def _secciones_markdown(texto: str) -> list[SeccionTexto]:
    """Divide el Markdown por encabezados (#, ##, ...) con rango de líneas.

    La sección conserva sus líneas de origen para que las citas puedan
    apuntar al documento original (§4.3: metadatos de trazabilidad).
    """
    lineas = texto.splitlines()
    limites: list[tuple[int, str]] = []  # (numero_de_linea_base_1, titulo)
    bloque = ""
    for indice, linea in enumerate(lineas, start=1):
        cerca = re.match(r"^ {0,3}(`{3,}|~{3,})", linea)
        if cerca:
            marcador = cerca.group(1)
            if not bloque:
                bloque = marcador
            elif marcador[0] == bloque[0] and len(marcador) >= len(bloque):
                bloque = ""
            continue
        if bloque:
            continue
        # Encabezado ATX: 1..6 "#" al inicio. El título es el resto sin #.
        coincidencia = re.match(r"^(#{1,6})\s+(.*)$", linea)
        if coincidencia:
            limites.append((indice, coincidencia.group(2).strip()))

    secciones: list[SeccionTexto] = []
    if limites and limites[0][0] > 1:
        fin = limites[0][0] - 1
        secciones.append(SeccionTexto("(introducción)", 1, fin, "\n".join(lineas[:fin])))
    for posicion, (linea_inicio, titulo) in enumerate(limites):
        linea_fin = limites[posicion + 1][0] - 1 if posicion + 1 < len(limites) else len(lineas)
        # El texto de la sección EXCLUYE la línea del propio encabezado
        # siguiente (que abre la sección siguiente).
        cuerpo = "\n".join(lineas[linea_inicio:linea_fin])
        secciones.append(
            SeccionTexto(titulo=titulo, linea_inicio=linea_inicio, linea_fin=linea_fin, texto=cuerpo.strip())
        )

    if not secciones:
        # Markdown sin encabezados: una única sección con todo el contenido.
        secciones.append(
            SeccionTexto(titulo="(sin encabezados)", linea_inicio=1, linea_fin=len(lineas), texto=texto.strip())
        )
    return secciones


def _validar_texto_plano(contenido: bytes, extension: str, limites: LimitesIngesta) -> str:
    """Valida y decodifica MD/TXT: UTF-8 estricto y sin contenido binario.

    §11.3 exige validar el formato REAL: un .md que en realidad es una
    imagen o un binario debe rechazarse aunque la extensión diga lo
    contrario. UTF-8 estricto (errors="strict") rebienta si hay bytes
    inválidos; además se detecta la marca de texto verdaderamente binario
    (byte nulo) que UTF-8 solo no garantía.
    """
    try:
        texto = contenido.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ParserError(
            ParserErrorCode.FORMATO_NO_SOPORTADO,
            f"El archivo .{extension} no es texto UTF-8 valido.",
            detalles={"detalle_tecnico": str(error), "sugerencia": "Guardar el archivo como UTF-8 y volver a subirlo."},
        ) from error
    if any(ord(c) < 32 and c not in "\t\n\r" for c in texto) or "\x7f" in texto:
        raise ParserError(
            ParserErrorCode.BINARIO,
            f"El archivo .{extension} contiene bytes binarios (byte nulo); no es un documento de texto.",
            detalles={"sugerencia": "Subir el documento de texto original, no un archivo renombrado."},
        )
    return texto


def _rechazo_limite(codigo: ParserErrorCode, limite: int, recibido, unidad: str) -> ParserError:
    """Arma el error accionable de límites: qué límite, cuánto llegó, qué hacer."""
    return ParserError(
        codigo,
        f"El documento excede el limite de {unidad} ({limite:,}): recibido {recibido:,}. "
        "No se proceso nada: reducir el documento o pedir un alcance menor (nunca se trunca en silencio).",
        detalles={"limite": limite, "recibido": recibido, "unidad": unidad},
    )


# --------------------------------------------------------------------------
# Parseo PDF
# --------------------------------------------------------------------------


def _worker_pdf(canal, contenido: bytes, titulo: str, limites: LimitesIngesta) -> None:
    """Proceso descartable: memoria acotada en Linux (OCI)."""
    try:
        import sys

        if sys.platform == "linux":
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (limites.max_memoria_pdf_bytes, limites.max_memoria_pdf_bytes))
        canal.send((True, _parsear_pdf(contenido, titulo, limites)))
    except ParserError as error:
        canal.send((False, (error.codigo, error.mensaje, error.detalles)))
    except Exception:
        canal.send((False, (ParserErrorCode.CORRUPTO, "PDF ilegible o excede la memoria de extracción.", {})))
    finally:
        canal.close()


def _pdf_aislado(contenido: bytes, titulo: str, limites: LimitesIngesta) -> ResultadoParseo:
    contexto = multiprocessing.get_context("spawn")
    receptor, emisor = contexto.Pipe(duplex=False)
    proceso = contexto.Process(target=_worker_pdf, args=(emisor, contenido, titulo, limites), daemon=True)
    try:
        proceso.start()
        emisor.close()
        if not receptor.poll(limites.presupuesto_extraccion_segundos):
            raise ParserError(ParserErrorCode.TIMEOUT_EXTRACCION, "La extracción PDF excedió el tiempo permitido.")
        try:
            correcto, resultado = receptor.recv()
        except EOFError as error:
            raise ParserError(
                ParserErrorCode.CORRUPTO, "El proceso de extracción PDF terminó sin resultado."
            ) from error
        if not correcto:
            raise ParserError(*resultado)
        return resultado
    finally:
        if proceso.pid is not None:
            if proceso.is_alive():
                proceso.terminate()
            proceso.join()
            proceso.close()
        receptor.close()
        emisor.close()


def _parsear_pdf(contenido: bytes, titulo_etiqueta: str, limites: LimitesIngesta) -> ResultadoParseo:
    """Extrae texto por página de un PDF ya validado en firma y tamaño."""
    try:
        # PdfReader necesita un stream con seek: envolvemos los bytes en un
        # BytesIO EN MEMORIA. Nada toca el disco con el nombre del usuario
        # (§11.3: el nombre es etiqueta, nunca ruta).
        lector = PdfReader(io.BytesIO(contenido))
    except PdfReadError as error:
        raise ParserError(
            ParserErrorCode.CORRUPTO,
            f"No se pudo leer la estructura del PDF '{titulo_etiqueta}'.",
            detalles={
                "detalle_tecnico": str(error),
                "sugerencia": "Verificar que el archivo abra en un lector de PDF y volver a subirlo.",
            },
        ) from error

    if lector.is_encrypted:
        # §4.1: "cifrado sin acceso" se rechaza con explicación; NO se
        # intentan contraseñas (no es nuestro problema descifrar).
        raise ParserError(
            ParserErrorCode.CIFRADO,
            f"El PDF '{titulo_etiqueta}' esta cifrado y no puede procesarse.",
            detalles={"sugerencia": "Subir una version sin contrasena del documento."},
        )

    # LÍMITE DE PÁGINAS ANTES DE EXTRAER (criterio del issue): contar
    # páginas es barato; extraer texto es lo caro. Si ya sabemos que el
    # documento no puede entrar, no gastamos el presupuesto.
    cantidad_paginas = len(lector.pages)
    if cantidad_paginas > limites.max_paginas:
        raise _rechazo_limite(ParserErrorCode.LIMITE_PAGINAS, limites.max_paginas, cantidad_paginas, "paginas")

    # Presupuesto de reloj compartido por TODA la extracción (§11.3).
    inicio = time.monotonic()
    paginas: list[PaginaTexto] = []
    paginas_visuales: list[int] = []
    for numero, pagina in enumerate(lector.pages, start=1):
        if time.monotonic() - inicio > limites.presupuesto_extraccion_segundos:
            raise ParserError(
                ParserErrorCode.TIMEOUT_EXTRACCION,
                "La extraccion de texto del PDF excedio el presupuesto de tiempo.",
                detalles={
                    "presupuesto_segundos": limites.presupuesto_extraccion_segundos,
                    "pagina_alcanzada": numero,
                    "sugerencia": "Subir un documento mas liviano o dividirlo.",
                },
            )
        try:
            texto = pagina.extract_text() or ""
        except PdfReadError:
            # Una página ilegible aislada no tumba el documento: queda
            # registrada como página visual/omitida en la cobertura.
            texto = ""
        texto = texto.strip()
        contenido_pagina = pagina.get_contents()
        operadores = [op for _, op in contenido_pagina.operations] if contenido_pagina is not None else []
        visual = b"Do" in operadores or sum(op in (b"S", b"s", b"f", b"f*", b"B", b"B*") for op in operadores) >= 3
        if len(texto) < limites.minimo_caracteres_por_pagina or visual:
            # Sin texto extraíble => diagrama o escaneo => contrato visual #30.
            paginas_visuales.append(numero)
        if texto:
            paginas.append(PaginaTexto(numero=numero, texto=texto))
        tokens_acumulados = estimar_tokens("\n".join(p.texto for p in paginas))
        if tokens_acumulados > limites.max_tokens_extraidos:
            raise _rechazo_limite(
                ParserErrorCode.LIMITE_TOKENS, limites.max_tokens_extraidos, tokens_acumulados, "tokens estimados"
            )

    if len(paginas_visuales) > limites.max_paginas_visuales:
        raise _rechazo_limite(
            ParserErrorCode.LIMITE_PAGINAS_VISUALES,
            limites.max_paginas_visuales,
            len(paginas_visuales),
            "paginas visuales",
        )

    texto_total = "\n".join(pagina.texto for pagina in paginas)
    tokens = estimar_tokens(texto_total)
    if tokens > limites.max_tokens_extraidos:
        # NUNCA truncar (§4.1): rechazo completo con código estable.
        raise _rechazo_limite(ParserErrorCode.LIMITE_TOKENS, limites.max_tokens_extraidos, tokens, "tokens estimados")

    # Documento escaneado puro (todas las páginas sin texto): NO se rechaza
    # por texto vacío (criterio del issue) — se deriva al contrato visual
    # de #30 y queda processing, sin declararse ready.
    requiere_vision = bool(paginas_visuales)
    es_escaneado_puro = requiere_vision and not paginas
    if es_escaneado_puro:
        estado = DocumentStatus.PROCESSING
        detalle = (
            f"Documento escaneado: {len(paginas_visuales)} paginas sin texto extraible quedan "
            "pendientes de interpretacion visual (issue #30)."
        )
    elif requiere_vision:
        estado = DocumentStatus.PROCESSING
        detalle = (
            f"{len(paginas_visuales)} pagina(s) contienen diagramas/imagenes sin texto "
            f"(paginas {paginas_visuales}); pendientes de interpretacion visual (issue #30)."
        )
    elif tokens < limites.minimo_tokens_documento:
        raise ParserError(
            ParserErrorCode.EXTRACCION_INSUFICIENTE,
            f"La extraccion del PDF '{titulo_etiqueta}' produjo demasiado poco texto ({tokens} tokens estimados).",
            detalles={"sugerencia": "Verificar que el PDF contenga texto seleccionable (no solo imagenes)."},
        )
    else:
        estado = DocumentStatus.READY
        detalle = None

    cobertura = (
        f"Paginas procesadas con texto: {len(paginas)} de {cantidad_paginas}. "
        f"Paginas visuales pendientes (issue #30): {len(paginas_visuales)}"
        + (f" ({paginas_visuales})." if paginas_visuales else ".")
        + f" Tokens estimados: {tokens:,}."
    )
    return ResultadoParseo(
        extension="pdf",
        titulo_etiqueta=titulo_etiqueta,
        hash_sha256=hashlib.sha256(contenido).hexdigest(),
        paginas=paginas,
        secciones=[],
        cantidad_tokens=tokens,
        paginas_visuales=paginas_visuales,
        requiere_vision=requiere_vision,
        estado_documento=estado,
        detalle_estado=detalle,
        cobertura=cobertura,
    )


# --------------------------------------------------------------------------
# Parseo MD / TXT
# --------------------------------------------------------------------------


def _parsear_texto(contenido: bytes, titulo_etiqueta: str, extension: str, limites: LimitesIngesta) -> ResultadoParseo:
    """MD/TXT: validar UTF-8/binario, secciones y límites de tokens."""
    texto = _validar_texto_plano(contenido, extension, limites)
    if not texto.strip("\ufeff \t\n\r"):
        raise ParserError(ParserErrorCode.EXTRACCION_INSUFICIENTE, "El archivo no contiene texto para procesar.")
    texto = texto.lstrip("\ufeff")
    tokens = estimar_tokens(texto)
    if tokens > limites.max_tokens_extraidos:
        raise _rechazo_limite(ParserErrorCode.LIMITE_TOKENS, limites.max_tokens_extraidos, tokens, "tokens estimados")

    secciones = (
        _secciones_markdown(texto)
        if extension == "md"
        else [
            SeccionTexto(
                titulo=titulo_etiqueta or "contenido",
                linea_inicio=1,
                linea_fin=len(texto.splitlines()),
                texto=texto.strip(),
            )
        ]
    )
    cobertura = (
        f"Documento de texto completo: {len(secciones)} seccion(es), "
        f"{len(texto.splitlines())} lineas, {tokens:,} tokens estimados."
    )
    return ResultadoParseo(
        extension=extension,
        titulo_etiqueta=titulo_etiqueta,
        hash_sha256=hashlib.sha256(contenido).hexdigest(),
        paginas=[],
        secciones=secciones,
        cantidad_tokens=tokens,
        estado_documento=DocumentStatus.READY,
        cobertura=cobertura,
    )


# --------------------------------------------------------------------------
# API pública del parser
# --------------------------------------------------------------------------


def parsear_archivo(contenido: bytes, nombre_etiqueta: str, limites: LimitesIngesta | None = None) -> ResultadoParseo:
    """Punto de entrada para archivos subidos (upload de #19).

    `contenido` son los BYTES crudos del archivo y `nombre_etiqueta` es el
    nombre que subió el usuario — usado únicamente como etiqueta/título y
    en mensajes de error, NUNCA para abrir rutas (§11.3). El orden de las
    validaciones va de lo más barato a lo más caro:

        1. tamaño (bytes que ya tenemos en memoria)
        2. firma/contenido real según formato
        3. límites que requieren abrir el documento (páginas, tokens)
        4. extracción con presupuesto de tiempo

    La extensión decide el LÍMITE de tamaño y el camino de parseo, pero la
    FIRMA decide si es aceptable: un .pdf sin firma %PDF se rechaza aunque
    la extensión lodeclare.
    """
    limites = limites or LimitesIngesta()
    nombre_limpio = (nombre_etiqueta or "documento").strip()
    extension = Path(nombre_limpio.lower()).suffix.lstrip(".")

    if extension == "pdf":
        if len(contenido) > limites.max_bytes_pdf:
            raise _rechazo_limite(ParserErrorCode.DEMASIADO_GRANDE, limites.max_bytes_pdf, len(contenido), "bytes")
        if not _firma_pdf(contenido):
            raise ParserError(
                ParserErrorCode.FORMATO_NO_SOPORTADO,
                f"El archivo '{nombre_limpio}' no tiene la firma de un PDF real (se esperaba '%PDF-').",
                detalles={"sugerencia": "Subir el archivo original sin renombrar su extension."},
            )
        return _pdf_aislado(contenido, nombre_limpio, limites)

    if extension in ("md", "markdown", "txt"):
        if len(contenido) > limites.max_bytes_texto:
            raise _rechazo_limite(ParserErrorCode.DEMASIADO_GRANDE, limites.max_bytes_texto, len(contenido), "bytes")
        return _parsear_texto(contenido, nombre_limpio, "md" if extension in ("md", "markdown") else "txt", limites)

    raise ParserError(
        ParserErrorCode.FORMATO_NO_SOPORTADO,
        f"Formato no soportado: '.{extension}'. Los formatos aceptados son PDF, Markdown (.md) y TXT.",
        detalles={"formatos_aceptados": ["pdf", "md", "markdown", "txt"]},
    )


def parsear_texto_pegado(
    documento_titulo: str, documento_contenido: str, limites: LimitesIngesta | None = None
) -> ResultadoParseo:
    """Entrada «pegar texto» (§4.1): se persiste como original TXT.

    "La carga de texto plano admite documento_titulo y documento_contenido;
    se transforma en un documento TXT con ID" (§16.1). El caller (#19)
    genera el ID y guarda ESTE resultado como si fuera un .txt subido: el
    hash se calcula sobre el contenido UTF-8, así que un texto pegado y un
    archivo .txt con el mismo contenido producen el MISMO hash (dedupe
    natural, §4.5).
    """
    limites = limites or LimitesIngesta()
    if not documento_contenido.strip():
        # Un "documento" vacío no tiene nada que adaptar: rechazo claro
        # (el contrato de entrada #03 ya exige min_length=1; esto cubre
        # a quien llame directo con espacios en blanco).
        raise ParserError(
            ParserErrorCode.EXTRACCION_INSUFICIENTE,
            "El texto pegado esta vacio: no hay contenido para procesar.",
            detalles={"sugerencia": "Escribi o pega el contenido del documento."},
        )
    contenido = documento_contenido.encode("utf-8")
    if len(contenido) > limites.max_bytes_texto:
        raise _rechazo_limite(ParserErrorCode.DEMASIADO_GRANDE, limites.max_bytes_texto, len(contenido), "bytes")
    return _parsear_texto(contenido, documento_titulo.strip() or "texto-sin-titulo", "txt", limites)
