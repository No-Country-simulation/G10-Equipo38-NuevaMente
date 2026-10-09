"""Reconstrucción del registro operativo desde objetos privados confirmados.

Se ejecuta al arrancar un registro nuevo o una reconstrucción pendiente, nunca por canje.
No restaura sesiones ni llama al LLM. Índices/visión requieren su reconstrucción.
"""

import hashlib
import json
from datetime import datetime, timezone

from app.core.rag.rebuild import ManifiestoDocumento
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.responses import PedagogicalOutput
from app.session.manifests import ManifiestoEspacio
from app.storage.object_keys import clave_documento, clave_original, clave_output, clave_progress, clave_workspace
from app.storage.provider import StorageNotFound, StorageProvider


def _objetos(storage: StorageProvider, prefijo: str):
    cursor, vistos = None, set()
    while True:
        pagina = storage.list(prefijo, limit=100, cursor=cursor)
        for objeto in pagina.items:
            if not objeto.object_name.startswith(prefijo):
                raise ValueError("Listado fuera del prefijo autorizado")
            yield objeto
        if pagina.next_cursor is None:
            return
        if pagina.next_cursor in vistos:
            raise ValueError("Cursor de storage repetido")
        vistos.add(pagina.next_cursor)
        cursor = pagina.next_cursor


def _progreso_vigente(valor, documentos: set[str], generaciones: set[str]):
    """Descarta eventos/agregados con referencias a recursos que no se restauraron."""
    if isinstance(valor, dict):
        if ("document_id" in valor and valor["document_id"] not in documentos) or (
            "generation_id" in valor and valor["generation_id"] not in generaciones
        ):
            return None
        resultado = {}
        for key, item in valor.items():
            filtrado = _progreso_vigente(item, documentos, generaciones)
            if filtrado is not None or item is None:
                resultado[key] = filtrado
        return resultado
    if isinstance(valor, list):
        return [
            filtrado for item in valor if (filtrado := _progreso_vigente(item, documentos, generaciones)) is not None
        ]
    return valor


def restaurar_registro(db: RegistroOperativo, storage: StorageProvider) -> int:
    if not db.recuperacion_pendiente():
        return 0
    restaurados = 0
    for objeto in _objetos(storage, "workspaces/"):
        if not objeto.object_name.endswith("/manifest.json"):
            continue
        manifest = ManifiestoEspacio.model_validate_json(storage.get(objeto.object_name, if_match=objeto.etag))
        workspace_id = manifest.workspace_id
        if objeto.object_name != clave_workspace(workspace_id):
            raise ValueError("Clave no canónica de espacio")
        if db.workspace_para_manifest(workspace_id) is not None:
            continue
        vigente = not (manifest.borrado or manifest.borrado_en) and manifest.expira_en > datetime.now(timezone.utc)
        datos = manifest.model_dump(mode="json")
        if not vigente:
            datos["borrado_en"] = datos["borrado_en"] or datetime.now(timezone.utc).isoformat()
        lapidas = {(lapida.recurso_tipo, lapida.recurso_id) for lapida in manifest.recursos_borrados}
        documentos, generaciones, progreso = [], [], None
        if vigente:
            for fuente in _objetos(storage, f"source_documents/{workspace_id}/"):
                if not fuente.object_name.endswith("/manifest.json"):
                    continue
                doc = ManifiestoDocumento.model_validate_json(storage.get(fuente.object_name, if_match=fuente.etag))
                if doc.workspace_id != workspace_id or fuente.object_name != clave_documento(
                    workspace_id, doc.document_id
                ):
                    raise ValueError("Manifiesto de documento ajeno o no canónico")
                if doc.borrado:
                    lapidas.add(("document", doc.document_id))
                if ("document", doc.document_id) in lapidas:
                    continue
                original = storage.get(clave_original(workspace_id, doc.document_id))
                if hashlib.sha256(original).hexdigest() != doc.hash_sha256:
                    raise ValueError("Original distinto al manifiesto confirmado")
                documentos.append(doc)
            por_id = {doc.document_id: doc for doc in documentos}
            for salida in _objetos(storage, f"outputs/{workspace_id}/"):
                if not salida.object_name.endswith("/content.json"):
                    continue
                generation_id = salida.object_name.split("/")[-2]
                if ("generation", generation_id) in lapidas:
                    continue
                paquete = PedagogicalOutput.model_validate_json(storage.get(salida.object_name, if_match=salida.etag))
                if salida.object_name != clave_output(workspace_id, paquete.generation_id):
                    raise ValueError("Paquete con identidad ajena o no canónica")
                if paquete.almacenamiento_oci.objeto_id != salida.object_name:
                    raise ValueError("Paquete con almacenamiento ajeno")
                doc = por_id.get(paquete.documento_fuente.document_id)
                if doc is None:
                    continue
                if paquete.documento_fuente.hash.removeprefix("sha256:") != doc.hash_sha256:
                    raise ValueError("Paquete de otra versión del documento")
                generaciones.append(paquete)
            try:
                progreso = json.loads(storage.get(clave_progress(workspace_id)))
            except StorageNotFound:
                pass
            if progreso is not None and not isinstance(progreso, dict):
                raise ValueError("Progreso inválido")
        # Revalidar tras leer los originales/paquetes, antes de publicar registros.
        storage.get(objeto.object_name, if_match=objeto.etag)
        with db.transaccion():
            if not db.restaurar_workspace(datos):
                continue
            for tipo, recurso_id in sorted(lapidas):
                lapida = next(
                    (
                        item
                        for item in manifest.recursos_borrados
                        if (item.recurso_tipo, item.recurso_id) == (tipo, recurso_id)
                    ),
                    None,
                )
                db.restaurar_lapida(
                    workspace_id,
                    tipo,
                    recurso_id,
                    lapida.solicitado_en.isoformat() if lapida else datetime.now(timezone.utc).isoformat(),
                    lapida.purga_despues_en.isoformat() if lapida else manifest.expira_en.isoformat(),
                )
            if not vigente:
                db.restaurar_lapida(workspace_id, "workspace", workspace_id, datos["borrado_en"], datos["expira_en"])
            for doc in documentos:
                # Ready solo después de reconstruir su índice y, cuando corresponda, visión.
                estado = DocumentStatus.FAILED if doc.estado == DocumentStatus.FAILED else DocumentStatus.PROCESSING
                db.registrar_documento(doc.document_id, workspace_id, doc.source_name, estado, doc.hash_sha256)
            for paquete in generaciones:
                db.registrar_generacion(
                    paquete.generation_id, workspace_id, paquete.documento_fuente.document_id, JobStatus.COMPLETED
                )
            if progreso is not None:
                # Preservar el agregado; #42 implementará su consumo por la UI.
                filtrado = _progreso_vigente(progreso, set(por_id), {paquete.generation_id for paquete in generaciones})
                if filtrado is not None:
                    db.guardar_progreso_restaurado(workspace_id, filtrado)
        restaurados += 1
    db.confirmar_recuperacion()
    return restaurados
