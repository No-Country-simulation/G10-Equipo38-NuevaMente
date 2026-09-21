"""Segmentación estructural trazable (issue #12, decisiones §4.3).

Objetivo: 750 tokens BPE con hasta 120 de solapamiento y tolerancia del 10%.
Encabezados, tablas, cercas de código y solapamiento cuentan contra el techo.
El solapamiento se reduce para conservar unidades; no cruza secciones/páginas.
Una fila indivisible mayor al techo se rechaza explícitamente, nunca se trunca.

La medida local cl100k_base no sustituye el contador de Gemini (#13).
Los IDs incluyen espacio, documento, contenido, parser, chunker y tokenizador.
start_index se mide en caracteres del texto normalizado a LF y sin BOM;
en PDF es relativo a la página. Las líneas citan el cuerpo original, no las
cabeceras/cercas repetidas como contexto. Chroma omite metadatos nulos.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, replace

from app.core.rag.parser import LimitesIngesta, ResultadoParseo
from app.core.rag.tokenizer import Tokenizador, TokenizadorBPE
from app.schemas.internal import Chunk as ChunkContratoInterno

# --------------------------------------------------------------------------
# Tokenizador explícito y configuración
# --------------------------------------------------------------------------

# Palabras y signos para la heurística de idioma; NO se usan para contar tokens.
_PALABRA = re.compile(r"[\w]+|[^\w\s]", re.UNICODE)


@dataclass(frozen=True)
class ConfigChunker:
    """Parámetros de §4.3 (750/120) más la tolerancia y la versión del algoritmo.

    `tolerancia_proporcion`: un chunk puede pasar el objetivo hasta un 10%
    (líneas/filas atómicas que no valía la pena cortar); NUNCA pasa de
    objetivo*(1+tolerancia) (criterio 1 del issue).

    `version_algoritmo`: parte de la huella del chunk_id. Cambiar el
    algoritmo de corte ⇒ subir la versión ⇒ nuevos ids (los documentos ya
    citados con ids viejos no se corrompen: son ids distintos).
    """

    tamano_objetivo: int = 750
    solapamiento: int = 120
    tolerancia_proporcion: float = 0.10
    version_algoritmo: str = "v2"

    @property
    def tamano_maximo(self) -> int:
        """Techo duro: objetivo + tolerancia (criterio 1)."""
        return int(self.tamano_objetivo * (1 + self.tolerancia_proporcion))

    def huella(self, limites_parser: LimitesIngesta | None = None) -> str:
        """Huellita estable de la configuración (chunker + parser) para el id.

        §4.3: el chunk_id es estable "por versión del documento y
        configuración del parser": si cambia el límite de tokens o el
        tamaño objetivo, los cortes cambian y los ids DEBEN cambiar.
        """
        datos = {"chunker": asdict(self), "parser": asdict(limites_parser) if limites_parser else None}
        return hashlib.sha256(json.dumps(datos, sort_keys=True).encode()).hexdigest()

    def __post_init__(self):
        if type(self.tamano_objetivo) is not int or self.tamano_objetivo < 1:
            raise ValueError("tamano_objetivo debe ser un entero positivo")
        if type(self.solapamiento) is not int or not 0 <= self.solapamiento < self.tamano_objetivo:
            raise ValueError("solapamiento debe ser menor que el tamaño objetivo")
        if not math.isfinite(self.tolerancia_proporcion) or not 0 <= self.tolerancia_proporcion <= 1:
            raise ValueError("tolerancia_proporcion debe estar entre 0 y 1")
        if not self.version_algoritmo.strip():
            raise ValueError("Falta version_algoritmo")


# --------------------------------------------------------------------------
# Modelo del chunk (los metadatos completos de §4.3)
# --------------------------------------------------------------------------


@dataclass
class ChunkTexto:
    """Un chunk con todos sus metadatos de trazabilidad (§4.3).

    Líneas de vida de las citas:
    - PDF: `page` con la página; section_title None.
    - MD/TXT: `page` None; `section_title` y `linea_inicio`/`linea_fin`
      apuntan al archivo original (las citas de «Ver la fuente» y los
      Referencia del contrato #03 usan exactamente esto).
    """

    chunk_id: str
    workspace_id: str
    document_id: str
    document_hash: str
    source_name: str
    source_type: str  # "pdf" | "md" | "txt"
    chunk_index: int
    start_index: int  # Caracteres del texto normalizado; en PDF, relativos a la página.
    texto: str
    tokens: int
    page: int | None = None
    section_title: str | None = None
    linea_inicio: int | None = None
    linea_fin: int | None = None
    language: str = "es"
    tokenizer: str = ""

    def metadatos_chroma(self) -> dict[str, str | int | float | bool]:
        """Metadatos adaptados a lo que Chroma acepta (sin None).

        Chroma (#17) exige valores escalares: los campos que no aplican
        (page en MD/TXT, sección en PDF) se OMITEN en esta vista; el
        modelo público los conserva como None (§4.3).
        """
        metadatos: dict[str, str | int | float | bool] = {
            "workspace_id": self.workspace_id,
            "document_id": self.document_id,
            "document_hash": self.document_hash,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "chunk_index": self.chunk_index,
            "start_index": self.start_index,
            "language": self.language,
            "tokenizer": self.tokenizer,
        }
        if self.page is not None:
            metadatos["page"] = self.page
        if self.section_title is not None:
            metadatos["section_title"] = self.section_title
        if self.linea_inicio is not None:
            metadatos["linea_inicio"] = self.linea_inicio
        if self.linea_fin is not None:
            metadatos["linea_fin"] = self.linea_fin
        return metadatos

    def como_chunk_interno(self) -> ChunkContratoInterno:
        """Adaptador al `Chunk` del contrato interno (#03): quien ya consume
        esa forma (fixtures, evidencia del retriever) no cambia nada."""
        return ChunkContratoInterno(
            chunk_id=self.chunk_id,
            document_id=self.document_id,
            indice=self.chunk_index,
            texto=self.texto,
            pagina=self.page,
            seccion=self.section_title,
            cantidad_tokens=self.tokens,
            workspace_id=self.workspace_id,
            document_hash=self.document_hash,
            source_name=self.source_name,
            source_type=self.source_type,
            start_index=self.start_index,
            linea_inicio=self.linea_inicio,
            linea_fin=self.linea_fin,
            language=self.language,
            tokenizer=self.tokenizer,
            es_diagrama=False,  # las páginas visuales no generan chunks de texto
        )


# --------------------------------------------------------------------------
# Detección de idioma (metadata, §17.2)
# --------------------------------------------------------------------------

# Palabras muy frecuentes por idioma: suficiente para una heurística HONESTA
# y documentada; no un clasificador. Empate o vacío => "es" (default del
# proyecto, §17.1), y el caller siempre puede forzar el idioma conocido.
_FRECUENTES = {
    "es": {"el", "la", "los", "las", "de", "que", "y", "en", "un", "una", "con", "para", "por", "como", "se"},
    "en": {"the", "of", "and", "to", "in", "is", "a", "with", "for", "as", "that", "this", "are", "on"},
    "pt": {"de", "que", "e", "em", "um", "uma", "com", "para", "por", "como", "os", "as", "do", "da"},
}


def detectar_idioma(texto: str) -> str:
    """Heurística es/en/pt por palabras frecuentes (metadata de §17.2)."""
    palabras = [palabra.lower() for palabra in _PALABRA.findall(texto)]
    if not palabras:
        return "es"
    puntajes = {idioma: sum(1 for p in palabras if p in frecuentes) for idioma, frecuentes in _FRECUENTES.items()}
    return max(puntajes, key=lambda idioma: puntajes[idioma])


# --------------------------------------------------------------------------
# Bloques con trazabilidad de líneas
# --------------------------------------------------------------------------


@dataclass
class _Bloque:
    """Unidad indivisible de corte: párrafo, tabla o bloque de código.

    Conserva su rango de líneas original (MD/TXT) para que el chunk herede
    la cita exacta. `es_tabla` marca bloques cuya primera línea es la
    cabecera que hay que repetir si se parten (§4.3).
    """

    lineas: list[str]
    linea_inicio: int | None
    linea_fin: int | None
    es_tabla: bool = False
    page: int | None = None
    section_title: str | None = None

    start_index: int = 0
    prefijo: str = ""
    sufijo: str = ""

    @property
    def texto(self) -> str:
        return self.prefijo + "\n".join(self.lineas) + self.sufijo


def _es_linea_tabla(linea: str) -> bool:
    """Fila markdown de tabla: | celda | celda | (§4.3: límites lógicos)."""
    limpia = linea.strip()
    return "|" in limpia and bool(limpia.strip("|").strip())


class ChunkingError(ValueError):
    """No es posible mantener contexto y límites; nunca se trunca contenido."""


def _fragmento(bloque: _Bloque, inicio: int, fin: int) -> _Bloque:
    cuerpo = "\n".join(bloque.lineas)
    base = bloque.linea_inicio
    return replace(
        bloque,
        lineas=[cuerpo[inicio:fin]],
        start_index=bloque.start_index + inicio,
        linea_inicio=base + cuerpo[:inicio].count("\n") if base is not None else None,
        linea_fin=base + cuerpo[:fin].rstrip("\n").count("\n") if base is not None else None,
    )


def _maximo_prefijo(texto: str, cabe) -> int:
    izquierda, derecha = 0, len(texto)
    while izquierda < derecha:
        medio = (izquierda + derecha + 1) // 2
        if cabe(texto[:medio]):
            izquierda = medio
        else:
            derecha = medio - 1
    return izquierda


def _corte_logico(texto: str, hasta: int) -> int:
    if hasta == len(texto):
        return hasta
    for separador in (r"\n\n", r"\n", r"(?<=[.!?;])\s+", r"\s+"):
        candidatos = list(re.finditer(separador, texto[:hasta]))
        for candidato in reversed(candidatos):
            corte = candidato.end()
            anterior = re.search(r"\S+$", texto[: candidato.start()])
            siguiente = re.match(r"\S+", texto[corte:])
            # Evitar separar negaciones y unidades cuando hay un límite anterior.
            if anterior and (
                anterior.group().lower() in {"no", "not", "não", "sin", "excepto"}
                or (siguiente and siguiente.group() in {"ms", "s", "MB", "GB", "KB", "%", "kg"})
            ):
                continue
            if corte > 0:
                return corte
    return hasta  # Una secuencia sin separadores exige corte por caracteres Unicode.


def _partir_bloque(bloque: _Bloque, config: ConfigChunker, contar, contexto: str = "") -> list[_Bloque]:
    cuerpo = "\n".join(bloque.lineas)

    def envolver(texto):
        return contexto + bloque.prefijo + texto + bloque.sufijo

    if not cuerpo:
        raise ChunkingError("La cabecera o cerca vacía excede el límite de tokens")
    presupuesto = config.tamano_objetivo - config.solapamiento
    if contar(envolver("")) >= presupuesto:
        presupuesto = config.tamano_maximo
    partes = []
    inicio = 0
    while inicio < len(cuerpo):
        restante = cuerpo[inicio:]
        corte = _maximo_prefijo(restante, lambda texto: contar(envolver(texto)) <= presupuesto)
        if corte == 0:
            raise ChunkingError("El encabezado o contexto no deja espacio para contenido; aumentar el tamaño del chunk")
        corte = _corte_logico(restante, corte)
        if bloque.es_tabla:
            # Las filas son atómicas: no fabricar una tabla con celdas cortadas.
            salto = restante.rfind("\n", 0, corte + 1)
            if corte < len(restante):
                corte = salto + 1 if salto >= 0 else (restante.find("\n") + 1 or len(restante))
            if contar(envolver(restante[:corte])) > config.tamano_maximo:
                raise ChunkingError(
                    "Una fila de tabla y su cabecera exceden el límite; reducir la fila o ampliar el tamaño"
                )
        partes.append(_fragmento(bloque, inicio, inicio + corte))
        inicio += corte
    return partes


def _bloques_de_lineas(
    lineas: list[str], linea_base: int | None, page: int | None, section_title: str | None, start_index: int = 0
) -> list[_Bloque]:
    bloques = []
    offsets = [start_index]
    for linea in lineas:
        offsets.append(offsets[-1] + len(linea) + 1)

    def emitir(inicio, fin, prefijo="", sufijo="", tabla=False):
        bloques.append(
            _Bloque(
                lineas[inicio:fin],
                linea_base + inicio if linea_base is not None else None,
                linea_base + max(inicio, fin - 1) if linea_base is not None else None,
                es_tabla=tabla,
                page=page,
                section_title=section_title,
                start_index=offsets[inicio],
                prefijo=prefijo,
                sufijo=sufijo,
            )
        )

    def tabla(i):
        return (
            i + 1 < len(lineas)
            and _es_linea_tabla(lineas[i])
            and "|" in lineas[i + 1]
            and all(re.fullmatch(r":?-{3,}:?", celda.strip()) for celda in lineas[i + 1].strip().strip("|").split("|"))
        )

    i = 0
    while i < len(lineas):
        if not lineas[i].strip():
            i += 1
            continue
        cerca = re.match(r"^ {0,3}(`{3,}|~{3,})", lineas[i])
        if cerca:
            marcador = cerca.group(1)
            fin = i + 1
            while fin < len(lineas) and not re.fullmatch(
                r" {0,3}" + re.escape(marcador[0]) + "{" + str(len(marcador)) + r",}\s*", lineas[fin]
            ):
                fin += 1
            emitir(i + 1, fin, lineas[i] + "\n", "\n" + (lineas[fin] if fin < len(lineas) else marcador))
            i = min(fin + 1, len(lineas))
        elif tabla(i):
            fin = i + 2
            while fin < len(lineas) and _es_linea_tabla(lineas[fin]):
                fin += 1
            if fin == i + 2:
                emitir(i, fin)
            else:
                emitir(i + 2, fin, "\n".join(lineas[i : i + 2]) + "\n", tabla=True)
            i = fin
        else:
            fin = i + 1
            while (
                fin < len(lineas)
                and lineas[fin].strip()
                and not tabla(fin)
                and not re.match(r"^ {0,3}(`{3,}|~{3,})", lineas[fin])
            ):
                fin += 1
            emitir(i, fin)
            i = fin
    return bloques


def _cola(bloque: _Bloque, limite: int, contar) -> list[_Bloque]:
    if not limite or bloque.prefijo or bloque.sufijo:
        return []  # Tablas/código repiten sus propios encabezados, no fragmentos malformados.
    texto = "\n".join(bloque.lineas)
    largo = _maximo_prefijo(texto[::-1], lambda t: contar(t[::-1]) <= limite)
    inicio = len(texto) - largo
    if inicio and inicio < len(texto) and not texto[inicio - 1].isspace():
        siguiente = re.search(r"\s+", texto[inicio:])
        inicio = inicio + siguiente.end() if siguiente else len(texto)
    return [_fragmento(bloque, inicio, len(texto))] if inicio < len(texto) else []


def trocear(
    resultado: ResultadoParseo,
    workspace_id: str,
    document_id: str,
    source_name: str | None = None,
    language: str | None = None,
    config: ConfigChunker | None = None,
    tokenizador: Tokenizador | None = None,
    huella_config_parser: str | None = None,
) -> list[ChunkTexto]:
    config = config or ConfigChunker()
    tokenizador = tokenizador or TokenizadorBPE()
    if not workspace_id.strip() or not document_id.strip() or not resultado.hash_sha256:
        raise ValueError("Falta la identidad del espacio o documento")
    if not tokenizador.identidad.strip():
        raise ValueError("El tokenizador debe declarar una identidad versionada")
    contar = tokenizador.contar
    idioma = language or detectar_idioma(
        "\n".join(p.texto for p in resultado.paginas) + "\n".join(s.texto for s in resultado.secciones)
    )
    if idioma not in {"es", "en", "pt", "mixto"}:
        raise ValueError("Idioma no soportado")
    nombre = source_name or resultado.titulo_etiqueta
    identidad = [
        workspace_id,
        document_id,
        resultado.hash_sha256,
        config.huella(),
        huella_config_parser or resultado.huella_config_parser,
        tokenizador.identidad,
        nombre,
        idioma,
    ]
    grupos = []
    if resultado.extension == "pdf":
        for pagina in resultado.paginas:
            grupos.append((_bloques_de_lineas(pagina.texto.split("\n"), None, pagina.numero, None), ""))
    elif resultado.extension in {"md", "txt"}:
        for seccion in resultado.secciones:
            contexto = (seccion.encabezado + "\n") if seccion.encabezado else ""
            bloques = _bloques_de_lineas(
                seccion.texto.split("\n"),
                seccion.cuerpo_linea_inicio or seccion.linea_inicio,
                None,
                seccion.titulo,
                seccion.start_index,
            )
            # Un encabezado sin cuerpo también es contenido del documento.
            if not bloques and seccion.encabezado:
                bloques = [
                    _Bloque(
                        [seccion.encabezado],
                        seccion.linea_inicio,
                        seccion.linea_inicio,
                        section_title=seccion.titulo,
                        start_index=max(0, seccion.start_index - len(seccion.encabezado) - 1),
                    )
                ]
                contexto = ""
            grupos.append((bloques, contexto))
    else:
        raise ValueError("Formato no soportado por el chunker")

    chunks = []
    for bloques, contexto in grupos:

        def render(paquete):
            return contexto + "\n\n".join(b.texto for b in paquete)

        piezas = []
        for bloque in bloques:
            if contar(render([bloque])) <= config.tamano_maximo:
                piezas.append(bloque)
            else:
                piezas.extend(_partir_bloque(bloque, config, contar, contexto))
        paquete = []
        nuevo = False

        def cerrar():
            nonlocal paquete, nuevo
            if not nuevo:
                return
            texto = render(paquete)
            tokens = contar(texto)
            if tokens > config.tamano_maximo:
                raise ChunkingError("El contenido con su contexto excede el límite de tokens")
            primero, ultimo = paquete[0], paquete[-1]
            datos_id = [
                *identidad,
                len(chunks),
                primero.page,
                primero.section_title,
                primero.start_index,
                primero.linea_inicio,
                ultimo.linea_fin,
                texto,
            ]
            chunk_id = "chk_" + hashlib.sha256(json.dumps(datos_id, ensure_ascii=False).encode()).hexdigest()[:24]
            chunks.append(
                ChunkTexto(
                    chunk_id,
                    workspace_id,
                    document_id,
                    resultado.hash_sha256,
                    nombre,
                    resultado.extension,
                    len(chunks),
                    primero.start_index,
                    texto,
                    tokens,
                    primero.page,
                    primero.section_title,
                    primero.linea_inicio,
                    ultimo.linea_fin,
                    idioma,
                    tokenizador.identidad,
                )
            )
            paquete = _cola(ultimo, config.solapamiento, contar)
            nuevo = False

        for pieza in piezas:
            if nuevo and contar(render(paquete + [pieza])) > config.tamano_objetivo:
                cerrar()
            if not nuevo and contar(render(paquete + [pieza])) > config.tamano_maximo:
                paquete = []  # Se reduce solapamiento antes de romper una unidad estructural.
            paquete.append(pieza)
            nuevo = True
            if contar(render(paquete)) >= config.tamano_objetivo:
                cerrar()
        cerrar()
    return chunks
