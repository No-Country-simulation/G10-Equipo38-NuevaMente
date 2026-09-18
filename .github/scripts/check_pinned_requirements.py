"""Guardián de la política de versiones exactas (issue #02).

Por qué existe: decisiones_proyecto.md (§12.3 y §14.3) exige que TODOS los
requirements.txt del repo fijen versiones exactas con "==" (por ejemplo
``fastapi==0.115.0``). Los rangos (``>=``, ``~=``) permiten que dos personas
—o la CI y producción— instalen versiones distintas del mismo proyecto: el
clásico "en mi máquina funcionaba".

La CI llama a este script en cada PR (paso "Verificar versiones fijadas" de
.github/workflows/ci.yml). Falla con código de salida 1 si alguna línea
declara una dependencia flotante; imprime archivo y línea exactos para que
corregir sea trivial.

Uso:  python .github/scripts/check_pinned_requirements.py <requirements...>
Si no se pasan archivos, revisa los tres que administra el proyecto.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Especificadores que NO seleccionan una única versión: si aparece alguno,
# la línea es "flotante". Se evalúan antes que "==" para que un caso como
# "paquete>=1.0" (que también contiene "==" en ninguna parte) no pase por
# error; el orden del chequeo en revisar_linea() lo garantiza.
ESPECIFICADORES_FLOTANTES = (">=", "<=", "~=", "===", "!=", ">", "<")

# Archivos que el proyecto administra cuando el llamador no indica ninguno.
REQUISITOS_POR_DEFECTO = (
    "backend/requirements.txt",
    "frontend/requirements.txt",
    "requirements-dev.txt",
)


def revisar_linea(linea: str) -> str | None:
    """Valida UNA línea de requirements. Devuelve el problema o None si está bien.

    Reglas:
    - Líneas vacías y comentarios (empiezan con #) están permitidas: así los
      requirements documentan su política sin romper el chequeo.
    - Toda dependencia debe tener un pin exacto ``paquete==X.Y.Z``.
    - Los comodines (``==1.*``) también son flotantes: no fijan una versión.
    - Las opciones de pip (líneas que empiezan con "-", p. ej. ``-r`` o
      ``-e``) no forman parte de la política del proyecto: se rechazan para
      que nadie esconda dependencias dentro de un include.
    """
    # Separar un comentario al final de línea: "paquete==1.0  # explicación".
    declaracion = linea.split(" #", 1)[0].strip()
    if not declaracion or declaracion.startswith("#"):
        return None

    if declaracion.startswith("-"):
        return f"opcion de pip no soportada por la politica: '{declaracion}'"

    for especificador in ESPECIFICADORES_FLOTANTES:
        if especificador in declaracion:
            return f"version no fijada (contiene '{especificador}'): '{declaracion}'"

    if "==" not in declaracion:
        return f"falta el pin exacto (==): '{declaracion}'"

    # La parte después de "==" es la versión exigida; un comodín como "2.*"
    # aceptaría cualquier 2.x, o sea que tampoco fija la versión.
    version = declaracion.split("==", 1)[1].strip()
    if "*" in version:
        return f"pin con comodin, no es una version exacta: '{declaracion}'"

    return None


def revisar_archivo(ruta: Path) -> list[str]:
    """Revisa un requirements completo y devuelve la lista de problemas."""
    if not ruta.is_file():
        return [f"no existe el archivo: {ruta}"]

    problemas = []
    for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), start=1):
        problema = revisar_linea(linea)
        if problema:
            problemas.append(f"{ruta}:{numero}: {problema}")
    return problemas


def main(argv: list[str]) -> int:
    """Punto de entrada. Devuelve 0 si todo está fijado, 1 si hay problemas.

    El código de salida es lo que la CI mira: 0 = paso verde, cualquier otra
    cosa = paso rojo y el PR queda bloqueado.
    """
    rutas = [Path(arg) for arg in argv[1:]] or [Path(p) for p in REQUISITOS_POR_DEFECTO]

    problemas = [problema for ruta in rutas for problema in revisar_archivo(ruta)]
    if problemas:
        print("Politica de versiones exactas VIOLADA (decisiones_proyecto.md 12.3/14.3):")
        for problema in problemas:
            print(f"  - {problema}")
        print("Corregir fijando la version exacta: paquete==X.Y.Z")
        return 1

    print(f"OK: {len(rutas)} archivo(s) con todas las dependencias fijadas con '=='")
    return 0


if __name__ == "__main__":
    # sys.exit propaga el codigo de main() al proceso: es lo que lee la CI.
    sys.exit(main(sys.argv))
