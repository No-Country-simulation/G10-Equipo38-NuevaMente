"""Extracción determinista de candidatos y ubicaciones; nunca genera definiciones."""

import hashlib
import re

from app.schemas.glossary import TerminoCandidato
from app.schemas.pedagogical import Referencia

DETECTOR_VERSION = "nm-terms-1.0.0"
MAX_TERMINOS = 64
_PATRONES = (
    (re.compile(r"`([^`\n]{1,100})`"), True),
    (re.compile(r"\b(?:[A-Z][A-Z0-9_-]{1,15})(?:\.[A-Za-z_][\w.]*)?\b"), True),
    (re.compile(r"\b(?:Oracle|Google|Amazon|Microsoft)(?:[ \t]+[A-Z][\w-]*){1,4}"), True),
    (re.compile(r"\b(?:IPv[46]|[A-Za-z]+_[A-Za-z0-9_]+|(?:[A-Z][a-z]+){2,}|[a-z]+(?:[A-Z][a-z0-9]*)+)\b"), True),
    (re.compile(r"\*\*([^*\n]{2,100})\*\*"), False),
    (re.compile(r"(?m)^#{1,6}[ \t]+(.{2,100})$"), False),
    (
        re.compile(r"\b([\wÀ-ÿ][\wÀ-ÿ-]*(?:[ \t]+[\wÀ-ÿ][\wÀ-ÿ-]*){0,5})[ \t]+(?:es|son|is|are|é|são)[ \t]+", re.I),
        False,
    ),
)
_RUIDO = {"PDF", "TXT", "MD", "NOTA", "IMPORTANTE", "EL", "LA", "LOS", "LAS"}


def detectar_candidatos(chunks, *, chequear=lambda: None):
    encontrados = {}
    for chunk in chunks:
        chequear()
        referencia = Referencia(
            document_id=getattr(chunk, "document_id", None),
            chunk_id=chunk.chunk_id,
            pagina=chunk.pagina,
            seccion=chunk.seccion,
        )
        for patron, identificador in _PATRONES:
            for match in patron.finditer(chunk.texto):
                termino = match.group(1) if patron.groups else match.group()
                termino = re.sub(r"\s+", " ", termino).strip()
                if not identificador:
                    termino = termino.strip(".,:;()")
                    termino = re.sub(r"^(?:Una?|Las?|Los?|The|An?|Uma?|Os|As)\s+", "", termino, flags=re.I)
                if not 2 <= len(termino) <= 100 or termino.upper() in _RUIDO:
                    continue
                clave = termino.casefold()
                if clave not in encontrados:
                    encontrados[clave] = dict(
                        id="term_" + hashlib.sha256(clave.encode()).hexdigest()[:24],
                        termino_original=termino,
                        es_identificador=identificador,
                        ubicaciones=[],
                    )
                dato = encontrados[clave]
                dato["es_identificador"] |= identificador
                if referencia not in dato["ubicaciones"] and len(dato["ubicaciones"]) < 8:
                    dato["ubicaciones"].append(referencia)
    # Los IDs no dependen del orden de chunking. El límite es visible en salida.
    ordenados = sorted(
        encontrados.values(), key=lambda t: (not t["es_identificador"], t["termino_original"].casefold())
    )
    return [TerminoCandidato.model_validate(t) for t in ordenados[:MAX_TERMINOS]], max(0, len(ordenados) - MAX_TERMINOS)
