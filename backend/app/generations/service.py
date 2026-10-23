"""Admisión idempotente y escritura confirmada; no posee otra cola ni retries."""

import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from app.core.agents.basic import ejecutar_basico
from app.core.agents.critic import DependenciasCritic
from app.core.agents.graph import DependenciasOrquestacion, ejecutar_en_worker
from app.core.agents.graph_state import DependenciasGrafo, EstadoGrafo
from app.core.agents.researcher import DependenciasResearcher, researcher
from app.core.agents.writer import DependenciasWriter
from app.core.rag.rebuild import _workspace_vigente
from app.core.rag.retriever import RetrieverMMR
from app.documents.cursor import CursorDocumental
from app.exports.router import ServicioExportaciones
from app.generations.catalog import CatalogoGeneraciones
from app.jobs.manager import ContextoEjecucion, TrabajoCanceladoError
from app.schemas.enums import JobStatus
from app.schemas.errors import ErrorAplicacion, ErrorBody, ErrorCode
from app.schemas.requests import GenerateRequest
from app.schemas.responses import GenerationJobResponse, PersistenciaInfo, cargar_paquete_json
from app.storage.object_keys import clave_output
from app.storage.provider import StorageConflict, StorageUnavailable


# con esta clase orquesto la creacion consulta cancelacion y persistencia de adaptaciones educativas
# conecto la base de datos operativa con el almacenamiento en la nube y el gestor de tareas asincronas
class ServicioGeneraciones:
    def __init__(self, db, storage, consulta, gestor, configuracion, generador=None, verificador=None):
        self.db, self.storage, self.consulta, self.gestor = db, storage, consulta, gestor
        self.cfg, self.generador, self.verificador = configuracion, generador, verificador
        self.retriever = (
            RetrieverMMR(consulta.indice, configuracion=configuracion) if consulta.indice is not None else None
        )
        self.cursor = CursorDocumental(db.ruta_db.parent / "generation-cursor.key")
        self.catalogo = CatalogoGeneraciones(db, storage, consulta, configuracion.oci_bucket_name)
        self.exports = ServicioExportaciones(self.catalogo)
        self.db.purgar_aprobados_expirados()

    # con esta funcion auxiliar valido que el token pertenezca a una sesion activa y vigente
    # si la credencial no es valida detengo la operacion emitiendo un error estandar de sesion
    def _sesion(self, token):
        sesion = self.db.obtener_sesion(token)
        if sesion is None:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión no está disponible.")
        return sesion

    def _lectura(self, token):
        return ContextoEjecucion(
            "consulta_generacion",
            self._sesion(token)["workspace_id"],
            "consulta",
            time.monotonic() + 30,
            espacio_vigente=lambda: self.db.obtener_sesion(token) is not None,
        )

    def _propia_db(self, token, generation_id):
        ws = self._sesion(token)["workspace_id"]
        fila = self.db.obtener_generacion(generation_id)
        if fila is None or fila["workspace_id"] != ws:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Generación no encontrada.")
        return fila

    def _propia(self, token, generation_id):
        fila = self._propia_db(token, generation_id)
        doc_ids = self.db.documentos_generacion(generation_id) or [fila["document_id"]]
        for d_id in doc_ids:
            self.consulta.detalle(token, d_id)
        try:
            manifest = _workspace_vigente(self.storage, self._lectura(token), fila["document_id"])
            if any(
                (r.get("recurso_tipo") == "generation" and r.get("recurso_id") == generation_id)
                or (r.get("recurso_tipo") == "document" and r.get("recurso_id") in doc_ids)
                for r in manifest.get("recursos_borrados", [])
            ):
                raise TrabajoCanceladoError()
        except TrabajoCanceladoError:
            self._sesion(token)
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Generación no encontrada.") from None
        return self._propia_db(token, generation_id)

    def canonico(self, token, generation_id):
        return self.catalogo.canonico(token, generation_id)

    # aqui consulto el estado actualizado de una generacion comprobando los permisos de acceso del usuario
    # si el proceso culmino con exito incluyo los datos del material aprobado y la confirmacion de persistencia en oci
    def estado(self, token, generation_id):
        fila = self._propia(token, generation_id)
        job = self.db.job_generacion(generation_id)
        ruta = f"/api/generations/{generation_id}"
        error = None
        if fila["status"] in {"failed", "cancelled", "rejected_quality"}:
            causa = fila["error_code"] or fila["status"].upper()
            codigo = ErrorCode.INVALID_STATE
            if causa == "STORAGE_UNAVAILABLE":
                codigo = ErrorCode.STORAGE_UNAVAILABLE
            elif causa in {"CUOTA_AGOTADA", "RATE_LIMITED", "PRESUPUESTO_AGOTADO"}:
                codigo = ErrorCode.RATE_LIMITED
            elif causa == "PROVIDER_UNAVAILABLE":
                codigo = ErrorCode.PROVIDER_UNAVAILABLE
            error = ErrorBody(code=codigo, message="El trabajo no se completó.", details={"causa": causa})
        activo = fila["status"] in {"queued", "running"}
        posicion = self.gestor.estado(job["job_id"]).get("posicion_cola") if activo and job else None
        return GenerationJobResponse(
            generation_id=generation_id,
            status=fila["status"],
            status_url=ruta,
            events_url=ruta + "/events",
            cancel_url=ruta + "/cancel" if activo else None,
            posicion_cola=posicion,
            error=error,
            contenido=self.canonico(token, generation_id) if fila["status"] == "completed" else None,
            persistencia=PersistenciaInfo(status_upload="completado", provider="mock" if self.cfg.mock_oci else "oci")
            if fila["status"] == "completed"
            else None,
        )

    # con este metodo solicito la interrupcion cooperativa de una generacion que este en cola o en ejecucion
    # delego la cancelacion al gestor de trabajos asegurando que los recursos ocupados se liberen correctamente
    def cancelar(self, token, generation_id):
        fila = self._propia(token, generation_id)
        if fila["status"] == "cancelled":
            return self.estado(token, generation_id)
        job = self.db.job_generacion(generation_id)
        if job is None or not self.gestor.cancelar(job["job_id"]):
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "El trabajo ya terminó.")
        return self.estado(token, generation_id)

    # aqui construyo la lista paginada del historial de generaciones aplicando filtros por perfil formato o fechas
    # utilizo cursores opacos firmados criptograficamente para garantizar una navegacion segura y eficiente
    def pagina(self, token, *, cursor=None, limite=20, **filtros):
        ws = self._sesion(token)["workspace_id"]
        filtros = {k: v for k, v in filtros.items() if v is not None}
        scope = ws + ":generaciones:" + hashlib.sha256(json.dumps(filtros, sort_keys=True).encode()).hexdigest()
        despues = self.cursor.decodificar(cursor, scope) if cursor else None
        filas = []
        while len(filas) <= limite:
            lote = self.db.pagina_generaciones(ws, despues=despues, limit=limite + 1, **filtros)
            if not lote:
                break
            for fila in lote:
                despues = (fila["creado_en"], fila["generation_id"])
                try:
                    filas.append(self._propia(token, fila["generation_id"]))
                except ErrorAplicacion as exc:
                    if exc.error.code != ErrorCode.NOT_FOUND:
                        raise
                if len(filas) > limite:
                    break
            if len(lote) < limite + 1:
                break
        siguiente = None
        if len(filas) > limite:
            ultimo = filas[limite - 1]
            siguiente = self.cursor.codificar(scope, ultimo["creado_en"], ultimo["generation_id"])
        items = []
        for fila in filas[:limite]:
            propia = self._propia(token, fila["generation_id"])
            items.append(
                {
                    "generation_id": propia["generation_id"],
                    "document_id": propia["document_id"],
                    "status": propia["status"],
                    "created_at": propia["creado_en"],
                    "parametros": json.loads(propia["parametros_json"]) if propia["parametros_json"] else None,
                }
            )
        return {"items": items, "next_cursor": siguiente}

    def _vigente(self, contexto, generation_id, document_id, paquete=None):
        contexto.chequear()
        fila = self.db.obtener_generacion(generation_id)
        if fila is None or (fila["workspace_id"], fila["document_id"]) != (contexto.workspace_id, document_id):
            raise TrabajoCanceladoError()
        doc_ids = self.db.documentos_generacion(generation_id) or [document_id]
        for d_id in doc_ids:
            ws = _workspace_vigente(self.storage, contexto, d_id)
            if any(
                (r.get("recurso_tipo") == "generation" and r.get("recurso_id") == generation_id)
                or (r.get("recurso_tipo") == "document" and r.get("recurso_id") == d_id)
                for r in ws.get("recursos_borrados", [])
            ):
                raise TrabajoCanceladoError()
            doc_obj = self.consulta.documento_grafo(contexto, d_id)
            if doc_obj is None:
                raise TrabajoCanceladoError()
        documento = self.consulta.documento_grafo(contexto, document_id)
        if documento is None:
            raise TrabajoCanceladoError()
        if paquete is not None:
            if documento.fuente != paquete.documento_fuente:
                raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La fuente del material cambió.")
            if paquete.documentos_fuente:
                for doc_f in paquete.documentos_fuente:
                    doc_g = self.consulta.documento_grafo(contexto, doc_f.document_id)
                    if doc_g is None or doc_g.fuente != doc_f:
                        raise ErrorAplicacion(ErrorCode.INVALID_STATE, "Una fuente secundaria del material cambió.")
        return documento

    # con esta funcion guardo el artefacto educativo definitivo en el almacenamiento de oracle cloud infrastructure (oci)
    # retengo una copia local durante veinticuatro horas para permitir descargas o reintentos ante fallas de red
    def _persistir(self, paquete, contexto, *, retener=True):
        self._vigente(contexto, paquete.generation_id, paquete.documento_fuente.document_id, paquete)
        texto = paquete.model_dump_json()
        if retener:
            self.db.guardar_aprobado(
                paquete.generation_id, texto, (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
            )
        clave = clave_output(contexto.workspace_id, paquete.generation_id)
        try:
            self.storage.upload(clave, texto, crear_solo=True)
        except StorageConflict:
            if self.storage.get(clave) != texto.encode():
                raise StorageUnavailable("El objeto no corresponde al material retenido") from None
        if self.storage.get(clave) != texto.encode():
            raise StorageUnavailable("La escritura no pudo confirmarse")
        self.exports.preparar(
            contexto.workspace_id,
            paquete,
            texto.encode(),
            lambda: self._vigente(contexto, paquete.generation_id, paquete.documento_fuente.document_id, paquete),
        )
        self._vigente(contexto, paquete.generation_id, paquete.documento_fuente.document_id, paquete)
        return PersistenciaInfo(status_upload="completado", provider="mock" if self.cfg.mock_oci else "oci")

    # aqui configuro la funcion ejecutable que correra dentro del hilo dedicado del gestor de trabajos
    # selecciono entre el modo de generacion basico de un solo agente o el flujo multiagente completo segun la peticion
    def _trabajo(self, solicitud, generation_id, creado_en):
        def ejecutar(contexto):
            grafo = DependenciasGrafo(
                contexto,
                lambda ws, doc: self.consulta.documento_grafo(contexto, doc) if ws == contexto.workspace_id else None,
            )
            investigador = DependenciasResearcher(
                grafo, self.retriever, self.consulta.version_parser(contexto, solicitud.document_id)
            )
            if solicitud.modo_generacion == "basico":
                return ejecutar_basico(
                    EstadoGrafo(
                        workspace_id=contexto.workspace_id,
                        generation_id=generation_id,
                        document_id=solicitud.document_id,
                        document_ids=list(solicitud.document_ids),
                        parametros=solicitud,
                        deadline=contexto.deadline,
                    ),
                    grafo,
                    investigador,
                    DependenciasWriter(grafo, self.generador, self.cfg),
                    self._persistir,
                    bucket=self.cfg.oci_bucket_name,
                    created_at=datetime.fromisoformat(creado_en),
                )
            deps = DependenciasOrquestacion(
                grafo,
                lambda estado: researcher(estado, investigador),
                DependenciasWriter(grafo, self.generador, self.cfg),
                DependenciasCritic(grafo, self.verificador, self.cfg, self.consulta.loader_critic(contexto)),
                self._persistir,
                bucket=self.cfg.oci_bucket_name,
                created_at=datetime.fromisoformat(creado_en),
            )
            return ejecutar_en_worker(
                EstadoGrafo(
                    schema_version="1.1" if "modo_generacion" in solicitud.model_fields_set else "1.0",
                    workspace_id=contexto.workspace_id,
                    generation_id=generation_id,
                    document_id=solicitud.document_id,
                    document_ids=list(solicitud.document_ids),
                    parametros=solicitud,
                    deadline=contexto.deadline,
                ),
                deps,
            )

        return ejecutar

    def _terminal(self, generation_id, status):
        if status != JobStatus.FAILED:
            self.db.descartar_aprobado(generation_id)

    @staticmethod
    def _clave(clave):
        if clave is not None and (not clave.strip() or len(clave) > 200):
            raise ErrorAplicacion(ErrorCode.INVALID_REQUEST, "Idempotency-Key inválida.")
        return clave.strip() if clave is not None else None

    # en este metodo principal inicio una nueva generacion pedagogica validando limites y garantizando idempotencia
    # registro la intencion en la base de datos y encolo la tarea para su procesamiento en segundo plano
    def generar(self, token, solicitud: GenerateRequest, clave=None):
        clave = self._clave(clave)
        ws = self._sesion(token)["workspace_id"]
        if solicitud.alcance.tipo == "espacio_completo":
            ready_docs = self.db.documentos_ready(ws)
            if not ready_docs:
                raise ErrorAplicacion(ErrorCode.NOT_FOUND, "No hay documentos listos en el espacio.")
            if len(ready_docs) > 5:
                raise ErrorAplicacion(
                    ErrorCode.VALIDATION_ERROR,
                    "El alcance espacio_completo supera el límite máximo de 5 documentos.",
                    details={"cantidad": len(ready_docs), "maximo": 5},
                )
            doc_ids = sorted([d["document_id"] for d in ready_docs])
            datos_sol = solicitud.model_dump()
            datos_sol["document_ids"] = doc_ids
            datos_sol["document_id"] = doc_ids[0]
            solicitud = GenerateRequest.model_validate(datos_sol)
        if len(solicitud.document_ids) > 5:
            raise ErrorAplicacion(
                ErrorCode.VALIDATION_ERROR,
                "La solicitud supera el límite máximo de 5 documentos fuente.",
                details={"cantidad": len(solicitud.document_ids), "maximo": 5},
            )
        for doc_id in solicitud.document_ids:
            self.consulta._listo(token, doc_id)
        cuerpo = json.dumps(solicitud.parametros_persistidos(), sort_keys=True, ensure_ascii=False)
        if clave is not None:
            with self.gestor.admision_serializada():
                previo = self.db.idempotencia_consultar(ws, "generate", clave, cuerpo)
            if previo is not None:
                if previo[0] == "conflicto":
                    raise ErrorAplicacion(ErrorCode.IDEMPOTENCY_CONFLICT, "La clave ya corresponde a otro cuerpo.")
                return self._propia(token, previo[1])
        if (
            self.generador is None
            or self.retriever is None
            or (solicitud.modo_generacion == "multiagente" and self.verificador is None)
        ):
            raise ErrorAplicacion(
                ErrorCode.PROVIDER_UNAVAILABLE, "Generación requiere proveedores y cuotas explícitos."
            )
        ctx = self._lectura(token)
        documento = self.consulta.documento_grafo(ctx, solicitud.document_id)
        if documento is None:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Documento no encontrado.")
        if solicitud.alcance.tipo == "seccion" and solicitud.alcance.seccion_id not in documento.secciones:
            raise ErrorAplicacion(ErrorCode.VALIDATION_ERROR, "La sección no pertenece al documento.")
        generation_id = "gen_" + uuid.uuid4().hex
        snapshot = []
        for d_id in solicitud.document_ids:
            d_grafo = self.consulta.documento_grafo(ctx, d_id)
            if d_grafo is None or d_grafo.fuente is None:
                raise ErrorAplicacion(ErrorCode.NOT_FOUND, f"Documento {d_id} no encontrado.")
            snapshot.append(d_grafo.fuente.model_dump(mode="json"))
        with self.gestor.admision_serializada():
            try:
                with self.db.transaccion():
                    if clave is not None:
                        resultado, anterior = self.db.idempotencia_iniciar(
                            ctx.workspace_id, "generate", clave, cuerpo, recurso_id=generation_id
                        )
                        if resultado == "conflicto":
                            raise ErrorAplicacion(
                                ErrorCode.IDEMPOTENCY_CONFLICT, "La clave ya corresponde a otro cuerpo."
                            )
                        if resultado == "duplicada":
                            return self._propia_db(token, anterior)
                    max_gen = getattr(self.cfg, "workspace_max_generations", None)
                    self.db.comprobar_limite_recursos(ctx.workspace_id, "generations", max_gen)
                    self.db.registrar_generacion(
                        generation_id,
                        ctx.workspace_id,
                        solicitud.document_id,
                        document_ids=list(solicitud.document_ids),
                        snapshot=snapshot,
                    )
                    self.db.guardar_parametros_generacion(generation_id, solicitud.parametros_persistidos())
                fila = self.db.obtener_generacion(generation_id)
                self.gestor.enqueue(
                    ctx.workspace_id,
                    "generation",
                    self._trabajo(solicitud, generation_id, fila["creado_en"]),
                    generation_id=generation_id,
                    document_id=solicitud.document_id,
                    document_ids=list(solicitud.document_ids),
                    al_terminar=lambda status: self._terminal(generation_id, status),
                )
                if clave is not None:
                    self.db.idempotencia_completar(ctx.workspace_id, "generate", clave, generation_id)
            except Exception:
                self.db.abandonar_reserva_generacion(ctx.workspace_id, generation_id, "generate", clave, eliminar=True)
                raise
        return self._propia_db(token, generation_id)

    # con esta ruta permito reintentar la persistencia de un material aprobado que no llego a guardarse en la nube
    # recupero el paquete retenido localmente y reintento la subida a oci sin incurrir en nuevas llamadas a los modelos
    def persistir(self, token, generation_id, clave=None):
        self._propia(token, generation_id)
        clave = self._clave(clave)
        with self.gestor.admision_serializada():
            fila = self._propia_db(token, generation_id)
            if fila["status"] == "completed":
                return fila
            job = self.db.job_generacion(generation_id)
            if fila["status"] in ("queued", "running"):
                if job is not None and job["tipo"] == "persist":
                    return fila
                raise ErrorAplicacion(ErrorCode.INVALID_STATE, "El trabajo todavía está en ejecución.")
            texto = self.db.aprobado_retenido(generation_id)
            if fila["status"] != "failed" or texto is None:
                raise ErrorAplicacion(
                    ErrorCode.INVALID_STATE, "No hay material retenido vigente; iniciar otra generación."
                )
            paquete = cargar_paquete_json(texto)
            operacion = "persist:" + generation_id
            if clave is not None:
                resultado, _ = self.db.idempotencia_iniciar(
                    fila["workspace_id"], operacion, clave, generation_id, recurso_id=generation_id
                )
                if resultado == "duplicada":
                    return fila
                if resultado == "conflicto":
                    raise ErrorAplicacion(ErrorCode.IDEMPOTENCY_CONFLICT, "Clave de persistencia incompatible.")

            def ejecutar(contexto):
                contexto.registrar_paso("persist", 1)
                self._persistir(paquete, contexto, retener=False)
                return paquete

            try:
                nuevo_job = None
                self.db.actualizar_generacion(generation_id, JobStatus.QUEUED)
                nuevo_job = self.gestor.enqueue(
                    fila["workspace_id"],
                    "persist",
                    ejecutar,
                    generation_id=generation_id,
                    document_id=fila["document_id"],
                    document_ids=self.db.documentos_generacion(generation_id) or [fila["document_id"]],
                    al_terminar=lambda status: self._terminal(generation_id, status),
                )
                if clave is not None:
                    self.db.idempotencia_completar(fila["workspace_id"], operacion, clave, generation_id)
            except Exception:
                if nuevo_job is None:
                    self.db.actualizar_generacion(
                        generation_id, JobStatus.FAILED, fila["error_code"], fila["error_message"]
                    )
                    self.db.abandonar_reserva_generacion(fila["workspace_id"], generation_id, operacion, clave)
                raise
        return self._propia_db(token, generation_id)
