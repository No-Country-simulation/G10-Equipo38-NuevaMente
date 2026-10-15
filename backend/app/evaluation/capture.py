"""Captura acotada y reanudable desde la API; no configura proveedores ni cuotas."""

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.evaluation.cli import import_package, load_json, write_json
from app.evaluation.models import EvaluationArtifact
from app.evaluation.report import validate_artifacts
from app.schemas.responses import PedagogicalOutput


def capture_cases(dataset, api_url, token_file, bindings_file, mode, out, *, transport=None, timeout=330):
    url = urlsplit(api_url)
    local = url.hostname in ("localhost", "127.0.0.1", "::1")
    if (
        url.scheme not in ("https", "http")
        or (url.scheme == "http" and not local)
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise ValueError("Usar un origen HTTPS o HTTP de loopback sin credenciales ni parámetros en la URL")
    token = Path(token_file).read_text(encoding="utf-8").strip()
    if not token or len(token) > 4096 or any(char.isspace() for char in token):
        raise ValueError("Archivo de sesión inválido")
    bindings = load_json(bindings_file)
    if not isinstance(bindings, dict) or set(bindings) != set(dataset.sources):
        raise ValueError("Los bindings deben identificar los tres documentos ready del espacio")
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", value) for value in bindings.values()
    ):
        raise ValueError("ID de documento inválido")
    out = Path(out)
    with httpx.Client(
        base_url=api_url.rstrip("/"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
        follow_redirects=False,
        transport=transport,
    ) as client:
        for case in dataset.cases:
            target = out / "artifacts" / f"{case.case_id}-r1.json"
            if target.exists():
                # Una captura reanudada verifica el material ya guardado.
                existing = EvaluationArtifact.model_validate(load_json(target))
                validate_artifacts(dataset, [existing])
                if existing.case_id != case.case_id or existing.mode != mode:
                    raise ValueError("La captura guardada corresponde a otro caso o modo del proveedor")
                if existing.source.hash.removeprefix("sha256:") != dataset.sources[case.source].sha256:
                    raise ValueError("La fuente cambió: conservar la captura anterior y preparar una nueva ronda")
                continue
            request = {"document_id": bindings[case.source], **case.parameters.model_dump(mode="json")}
            signature = {
                "dataset": dataset.model_dump(mode="json"),
                "case": case.case_id,
                "request": request,
                "mode": mode,
            }
            key = "eval60-" + hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
            try:
                response = client.post("/api/generate", json=request, headers={"Idempotency-Key": key})
                response.raise_for_status()
                generation_id = response.json().get("generation_id")
                if not isinstance(generation_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", generation_id):
                    raise ValueError("La API no devolvió un ID válido")
                deadline = time.monotonic() + timeout
                while True:
                    response = client.get(f"/api/generations/{generation_id}")
                    response.raise_for_status()
                    status = response.json().get("status")
                    if status not in ("queued", "running", "completed", "rejected_quality", "failed", "cancelled"):
                        raise ValueError("Estado de generación desconocido")
                    if status not in ("queued", "running"):
                        break
                    if time.monotonic() >= deadline:
                        raise ValueError("La captura excedió su plazo; retomar con la misma clave idempotente")
                    time.sleep(0.5)
                if status != "completed":
                    write_json(out / "incidentes" / f"{case.case_id}.json", {"case_id": case.case_id, "status": status})
                    raise ValueError("La API no ofrece material canónico revisable; no contar como falso rechazo")
                response = client.get(f"/api/exports/{generation_id}", params={"format": "json"})
                response.raise_for_status()
                package = PedagogicalOutput.model_validate(response.json())
                if (
                    package.generation_id != generation_id
                    or package.documento_fuente.document_id != request["document_id"]
                ):
                    raise ValueError("El paquete no corresponde a la generación/documento solicitados")
                import_package(dataset, case.case_id, package, mode, target.parent)
                print(f"Material capturado: {case.case_id}")
            except httpx.HTTPError:
                raise ValueError(
                    "La API falló; verificar sesión, documento, proveedor y cuotas. No se muestran respuestas privadas."
                ) from None
