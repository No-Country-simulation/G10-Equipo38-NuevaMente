"""Reserva idempotente de trabajos comunes, con el orden gestor → SQLite."""

import json
import uuid

from app.schemas.errors import ErrorAplicacion, ErrorCode


def admitir_trabajo(
    gestor,
    workspace_id,
    document_id,
    tipo,
    cuerpo,
    ejecutar,
    *,
    clave=None,
    cache_key=None,
    document_ids=None,
    scope_hash=None,
    preparar=lambda: None,
):
    if clave is not None and (not clave.strip() or not 1 <= len(clave) <= 128):
        raise ErrorAplicacion(ErrorCode.INVALID_REQUEST, "Idempotency-Key debe tener entre 1 y 128 caracteres.")
    db = gestor.store
    with gestor.admision_serializada():
        cache = db.job_cache_glosario(workspace_id, cache_key) if cache_key else None
        job_id = cache["job_id"] if cache else "job_" + uuid.uuid4().hex[:16]
        if clave is not None:
            with db.transaccion():
                estado, previo = db.idempotencia_iniciar(
                    workspace_id, tipo, clave, json.dumps(cuerpo, sort_keys=True, ensure_ascii=False), job_id
                )
                if estado == "conflicto":
                    raise ErrorAplicacion(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Clave idempotente reutilizada con otro cuerpo."
                    )
                if estado == "duplicada":
                    job_id = previo
            existente = db.obtener_job(job_id)
            if existente is not None:
                return existente
        elif cache is not None:
            return cache
        try:
            preparar()
            gestor.enqueue(
                workspace_id,
                tipo,
                ejecutar,
                job_id=job_id,
                document_id=document_id,
                cache_key=cache_key,
                document_ids=document_ids,
                scope_hash=scope_hash,
            )
        except Exception:
            if clave is not None:
                db.abandonar_intencion_job(workspace_id, tipo, clave, job_id)
            raise
        return db.obtener_job(job_id)
