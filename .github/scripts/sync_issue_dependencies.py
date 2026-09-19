"""Sincronizador automático de dependencias de issues en Markdown.

Lee todos los issues del repositorio, identifica cuáles están cerrados y
actualiza las casillas de verificación de dependencias (`- [x] #N` vs `- [ ] #N`)
en los cuerpos Markdown de cada issue.

Preserva intactas todas las demás casillas (como criterios de aceptación o tareas)
que no correspondan a referencias de dependencias hacia otros issues.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any

# Expresión regular que detecta únicamente referencias a issues en task lists Markdown:
# Ejemplos válidos:
#   - [ ] #10 - Infraestructura de tests
#   - [x] #3 - Contratos compartidos v1
#   - [ ] #4
# NO coincide con:
#   - [ ] Los 3 documentos de Issue 05 se parsean...
#   - [ ] Criterio de aceptación
PATRON_DEPENDENCIA_ISSUE = re.compile(r"^(\s*-\s*\[)([ xX])(\]\s*#)(\d+)(.*)$")


def sync_markdown_checkboxes(body: str, closed_issue_numbers: set[int]) -> tuple[str, bool]:
    """Actualiza las casillas de dependencias en un texto Markdown.

    Args:
        body: Texto original del issue.
        closed_issue_numbers: Conjunto de números de issues que están cerrados.

    Returns:
        Una tupla (nuevo_cuerpo, fue_modificado).
    """
    if not body:
        return body, False

    lineas = body.split("\n")
    modificado = False
    lineas_nuevas: list[str] = []

    for linea in lineas:
        match = PATRON_DEPENDENCIA_ISSUE.match(linea)
        if match:
            prefijo = match.group(1)
            marca_actual = match.group(2)
            separador = match.group(3)
            ref_num = int(match.group(4))
            resto = match.group(5)

            esta_cerrado = ref_num in closed_issue_numbers
            marca_esperada = "x" if esta_cerrado else " "

            # Normalizar para comparar (por si vino 'X' mayúscula)
            actual_normalizada = "x" if marca_actual.lower() == "x" else " "

            if actual_normalizada != marca_esperada:
                linea = f"{prefijo}{marca_esperada}{separador}{ref_num}{resto}"
                modificado = True

        lineas_nuevas.append(linea)

    return "\n".join(lineas_nuevas), modificado


def _crear_request(url: str, token: str, metodo: str = "GET", data: bytes | None = None) -> urllib.request.Request:
    """Crea una petición HTTP con los encabezados requeridos por la API de GitHub."""
    req = urllib.request.Request(url, data=data, method=metodo)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "NuevaMente-Issue-Sync")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return req


def fetch_all_issues(repo: str, token: str) -> list[dict[str, Any]]:
    """Obtiene todos los issues (abiertos y cerrados) del repositorio."""
    issues: list[dict[str, Any]] = []
    page = 1
    per_page = 100

    while True:
        url = f"https://api.github.com/repos/{repo}/issues?state=all&per_page={per_page}&page={page}"
        req = _crear_request(url, token)
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data:
                    break
                for item in data:
                    # La API de issues devuelve también Pull Requests; se descartan.
                    if "pull_request" not in item:
                        issues.append(item)
                if len(data) < per_page:
                    break
                page += 1
        except urllib.error.HTTPError as err:
            cuerpo = err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Error {err.code} al listar issues de {repo}: {cuerpo}") from err

    return issues


def update_issue_body(repo: str, issue_number: int, new_body: str, token: str) -> None:
    """Actualiza el cuerpo de un issue vía API REST de GitHub."""
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    payload = json.dumps({"body": new_body}).encode("utf-8")
    req = _crear_request(url, token, metodo="PATCH", data=payload)
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status not in (200, 201):
                raise RuntimeError(f"Respuesta inesperada {resp.status} al actualizar issue #{issue_number}")
    except urllib.error.HTTPError as err:
        cuerpo = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Error {err.code} al actualizar issue #{issue_number}: {cuerpo}") from err


def sync_repository_issues(
    repo: str,
    token: str,
    dry_run: bool = False,
    verbose: bool = True,
) -> int:
    """Sincroniza los checkboxes de dependencias entre todos los issues del repositorio.

    Returns:
        Cantidad de issues modificados.
    """
    if verbose:
        print(f"[*] Obteniendo issues del repositorio '{repo}'...")

    issues = fetch_all_issues(repo, token)
    closed_numbers = {iss["number"] for iss in issues if iss.get("state") == "closed"}

    if verbose:
        print(f"[+] Total de issues encontrados: {len(issues)}")
        print(f"[+] Issues cerrados ({len(closed_numbers)}): {sorted(closed_numbers)}")

    modificados = 0

    for iss in issues:
        num = iss["number"]
        body_original = iss.get("body") or ""
        nuevo_body, cambio = sync_markdown_checkboxes(body_original, closed_numbers)

        if cambio:
            modificados += 1
            if dry_run:
                print(f"[DRY-RUN] Issue #{num} requiere actualización de dependencias.")
            else:
                if verbose:
                    print(f"[*] Actualizando dependencias en issue #{num}...")
                update_issue_body(repo, num, nuevo_body, token)
                if verbose:
                    print(f"[OK] Issue #{num} actualizado exitosamente.")

    if verbose:
        accion = "detectados para actualizar" if dry_run else "actualizados"
        print(f"[COMPLETO] Total de issues {accion}: {modificados}.")

    return modificados


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sincronizar checkboxes de dependencias de issues en Markdown.")
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", "No-Country-simulation/G10-Equipo38-NuevaMente"),
        help="Repositorio en formato owner/repo (default: variable GITHUB_REPOSITORY o repo del proyecto).",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="Token de GitHub con permisos de issues:write (default: variable GITHUB_TOKEN).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ejecuta la comparación sin escribir cambios en GitHub.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    token = args.token
    if not token:
        # Intentar cargar desde C:\Users\dave3\DavidGeneral\CosasSecretas\.env.github en desarrollo local
        env_secret_path = r"C:\Users\dave3\DavidGeneral\CosasSecretas\.env.github"
        if os.path.exists(env_secret_path):
            with open(env_secret_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GITHUB_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break

    if not token:
        print("ERROR: Debe proporcionar un token de GitHub vía --token, GITHUB_TOKEN o archivo local.", file=sys.stderr)
        sys.exit(1)

    try:
        sync_repository_issues(repo=args.repo, token=token, dry_run=args.dry_run, verbose=True)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
