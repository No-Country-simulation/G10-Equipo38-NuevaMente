"""BPE local de referencia para segmentación; no estima la facturación de Gemini.

El adaptador de Gemini debe validar su límite con el contador del proveedor.
Vocabulario oficial versionado: no hay descargas ni llamadas al importar/contar.
"""

import base64
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import tiktoken

SHA256_VOCABULARIO = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"


class Tokenizador(Protocol):
    identidad: str  # Debe cambiar si cambian vocabulario, reglas o versión.

    def contar(self, texto: str) -> int: ...


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    datos = (Path(__file__).parent / "assets" / "cl100k_base.tiktoken").read_bytes()
    if hashlib.sha256(datos).hexdigest() != SHA256_VOCABULARIO:
        raise ValueError("El vocabulario del tokenizador está corrupto")
    ranks = {base64.b64decode(token): int(rank) for token, rank in (line.split() for line in datos.splitlines())}
    return tiktoken.Encoding(
        name="cl100k_base",
        pat_str=r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s",
        mergeable_ranks=ranks,
        special_tokens={},  # Los marcadores recibidos son texto del documento, no instrucciones.
    )


class TokenizadorBPE:
    identidad = f"tiktoken-0.12.0:cl100k_base:{SHA256_VOCABULARIO}"

    def contar(self, texto: str) -> int:
        return len(_encoding().encode_ordinary(texto))
