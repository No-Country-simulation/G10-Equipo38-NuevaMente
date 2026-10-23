"""Reconstrucción del registro operativo desde objetos privados confirmados.

Se ejecuta al arrancar un registro nuevo o una reconstrucción pendiente, nunca por canje.
No restaura sesiones ni llama al LLM. Índices/visión requieren su reconstrucción.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone

from app.core.rag.parser import ParserError, validar_entrada_archivo
from app.core.rag.rebuild import ManifiestoDocumento
from app.core.rag.vectorstore import VectorStoreChroma
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.responses import cargar_paquete_json, modo_paquete
from app.session.manifests import ManifiestoEspacio
from app.storage.object_keys import clave_documento, clave_original, clave_output, clave_progress, clave_workspace
from app.storage.provider import StorageNotFound, StorageProvider

logger = logging.getLogger("nuevamente.session.recovery")


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
    retirados_locales = VectorStoreChroma.lapidas_persistidas(db.ruta_db.parent / "chroma")
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
        documentos_retirados_locales = {doc for ws, doc in retirados_locales if ws == workspace_id}
        lapidas.update(("document", doc) for doc in documentos_retirados_locales)
        documentos, generaciones, progreso = [], [], None
        evidencias = {}
        manifiestos_retirados = {}
        generaciones_retiradas = []
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
                    manifiestos_retirados[doc.document_id] = (fuente.etag, doc)
                    continue
                if not doc.admision_recuperable:
                    continue
                nueva = doc.admision_confirmada is True and doc.admision_sqlite_confirmada is True
                try:
                    original = storage.get(clave_original(workspace_id, doc.document_id))
                except StorageNotFound:
                    if not nueva:
                        raise
                    logger.warning("Fuente restaurable ausente; documento no autorizado")
                    continue
                if hashlib.sha256(original).hexdigest() != doc.hash_sha256:
                    if nueva:
                        logger.warning("Hash restaurable inconsistente; documento no autorizado")
                        continue
                    raise ValueError("Original distinto al manifiesto confirmado")
                if nueva:
                    try:
                        extension = validar_entrada_archivo(original, doc.source_name, doc.parser)
                        if doc.metadatos is None or (
                            doc.metadatos.tamano_bytes != len(original) or doc.metadatos.extension != extension
                        ):
                            raise ValueError("Metadata de fuente inconsistente")
                    except (ParserError, ValueError):
                        logger.warning("Metadata restaurable inconsistente; documento no autorizado")
                        continue
                    evidencias[doc.document_id] = {"etag": fuente.etag, "manifest": doc.model_dump(mode="json")}
                documentos.append(doc)
            por_id = {doc.document_id: doc for doc in documentos}
            for salida in _objetos(storage, f"outputs/{workspace_id}/"):
                if not salida.object_name.endswith("/content.json"):
                    continue
                generation_id = salida.object_name.split("/")[-2]
                paquete = cargar_paquete_json(storage.get(salida.object_name, if_match=salida.etag))
                if salida.object_name != clave_output(workspace_id, paquete.generation_id):
                    raise ValueError("Paquete con identidad ajena o no canónica")
                if paquete.almacenamiento_oci.objeto_id != salida.object_name:
                    raise ValueError("Paquete con almacenamiento ajeno")
                fuentes = paquete.documentos_fuente or [paquete.documento_fuente]
                alguna_lapida = any(("document", f.document_id) in lapidas for f in fuentes)
                if alguna_lapida:
                    for f in fuentes:
                        if ("document", f.document_id) in lapidas:
                            retirado = manifiestos_retirados.get(f.document_id)
                            if retirado is not None and (f.hash.removeprefix("sha256:") != retirado[1].hash_sha256):
                                raise ValueError("Paquete de otra versión del documento retirado")
                    # Relación de control para purgar sus derivados; la lápida del
                    # documento impide servir el resultado. Se registra INCLUSO cuando
                    # la generación tiene lápida propia: sin esta relación la purga de
                    # la recuperación desconoce sus derivados y jamás los elimina.
                    generaciones_retiradas.append(paquete)
                    continue

                if ("generation", generation_id) in lapidas:
                    continue

                todas_presentes = True
                for f in fuentes:
                    doc = por_id.get(f.document_id)
                    if doc is None:
                        todas_presentes = False
                        break
                    if f.hash.removeprefix("sha256:") != doc.hash_sha256:
                        raise ValueError("Paquete de otra versión del documento")
                if not todas_presentes:
                    continue

                generaciones.append(paquete)
            try:
                progreso = json.loads(storage.get(clave_progress(workspace_id)))
            except StorageNotFound:
                pass
            if progreso is not None and not isinstance(progreso, dict):
                raise ValueError("Progreso inválido")
        # Revalidar tras leer los originales/paquetes, antes de publicar registros.
        storage.get(objeto.object_name, if_match=objeto.etag)
        for doc in documentos:
            if doc.document_id in evidencias:
                storage.get(
                    clave_documento(workspace_id, doc.document_id), if_match=evidencias[doc.document_id]["etag"]
                )
                if (
                    hashlib.sha256(storage.get(clave_original(workspace_id, doc.document_id))).hexdigest()
                    != doc.hash_sha256
                ):
                    raise ValueError("La fuente cambió durante su restauración")
        storage.get(objeto.object_name, if_match=objeto.etag)
        for document_id, (etag, _) in manifiestos_retirados.items():
            storage.get(clave_documento(workspace_id, document_id), if_match=etag)
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
                if tipo == "document" and recurso_id in documentos_retirados_locales and lapida is None:
                    # Una retirada que sobrevivió en el catálogo no se presume
                    # sincronizada: reservar su confirmación remota existente.
                    db.agendar_borrado(tipo, recurso_id, workspace_id)
                    continue
                db.restaurar_lapida(
                    workspace_id,
                    tipo,
                    recurso_id,
                    lapida.solicitado_en.isoformat() if lapida else datetime.now(timezone.utc).isoformat(),
                    lapida.purga_despues_en.isoformat() if lapida else manifest.expira_en.isoformat(),
                )
            if not vigente:
                db.restaurar_lapida(workspace_id, "workspace", workspace_id, datos["borrado_en"], datos["expira_en"])
            for tipo, document_id in sorted(lapidas):
                if tipo != "document":
                    continue
                retirado = manifiestos_retirados.get(document_id)
                # document_cleanup requiere una fila de control. No restaurar
                # esta relación abandona la purga tras perder SQLite, incluso
                # cuando la lápida remota conserva correctamente el 404.
                # El hash puede ser desconocido si el manifiesto ya se purgó.
                db.registrar_documento(
                    document_id,
                    workspace_id,
                    "Documento retirado",
                    DocumentStatus.FAILED,
                    retirado[1].hash_sha256 if retirado else None,
                )
                db.agendar_efecto_documental(workspace_id, document_id)
            for doc in documentos:
                # Ready solo después de reconstruir su índice y, cuando corresponda, visión.
                estado = DocumentStatus.FAILED if doc.estado == DocumentStatus.FAILED else DocumentStatus.PROCESSING
                db.registrar_documento(
                    doc.document_id,
                    workspace_id,
                    doc.titulo or doc.source_name,
                    estado,
                    doc.hash_sha256,
                    source_name=doc.source_name,
                    metadatos=doc.metadatos,
                    cobertura=doc.cobertura,
                )
                if doc.document_id in evidencias:
                    db.acreditar_documento_restaurado(doc.document_id, evidencias[doc.document_id])
            for paquete in generaciones:
                fuentes = paquete.documentos_fuente or [paquete.documento_fuente]
                doc_ids = [f.document_id for f in fuentes]
                db.registrar_generacion(
                    paquete.generation_id,
                    workspace_id,
                    paquete.documento_fuente.document_id,
                    JobStatus.COMPLETED,
                    document_ids=doc_ids,
                    snapshot=fuentes,
                )
                datos = paquete.metadatos.model_dump(mode="json")
                db.guardar_parametros_generacion(
                    paquete.generation_id,
                    {
                        "document_id": paquete.documento_fuente.document_id,
                        **({"document_ids": doc_ids} if len(doc_ids) > 1 else {}),
                        **({"modo_generacion": modo_paquete(paquete)} if paquete.schema_version == "1.1" else {}),
                        "perfil_destinatario": datos["perfil_aplicado"],
                        "formato_salida": datos["formato_generado"],
                        "nicho_sector": datos["nicho_sector"],
                        "nivel_detalle": datos["nivel_detalle"],
                        "idioma_salida": datos["idioma_salida"],
                        "alcance": {k: v for k, v in datos["alcance"].items() if k != "secciones_cubiertas"},
                    },
                    creado_en=paquete.created_at.isoformat(),
                )
            for paquete in generaciones_retiradas:
                fuentes = paquete.documentos_fuente or [paquete.documento_fuente]
                doc_ids = [f.document_id for f in fuentes]
                db.registrar_generacion_retirada(
                    paquete.generation_id,
                    workspace_id,
                    paquete.documento_fuente.document_id,
                    document_ids=doc_ids,
                )
            if progreso is not None:
                # Preservar el agregado; #42 implementará su consumo por la UI.
                filtrado = _progreso_vigente(progreso, set(por_id), {paquete.generation_id for paquete in generaciones})
                if filtrado is not None:
                    db.guardar_progreso_restaurado(workspace_id, filtrado)
        restaurados += 1
    db.confirmar_recuperacion()
    return restaurados
