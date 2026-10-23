"""Punto de entrada del servicio backend (issue #07, referencia §7.1 y §7.3).

Qué hace este archivo: construye la aplicación FastAPI con TODO lo que el
issue pide como esqueleto:

1. ``GET /api/health`` — disponibilidad básica, sin secretos ni llamadas
   LLM (§7.1). Es lo que el proxy y el orquestador usan para saber si el
   servicio vive.
2. Middleware ``request_id`` — a CADA petición le asigna un identificador
   (o acepta el del cliente si viene en la cabecera X-Request-ID), lo
   devuelve en la respuesta y lo deja disponible para los logs (§11.5:
   los logs registran request_id, etapa, duración y código de error).
3. Handlers de excepciones — cualquier fallo responde con el envoltorio
   ÚNICO del contrato (``error.code/message/details`` + ``request_id``,
   definido en app/schemas/errors.py por el issue #03), con el mapeo HTTP
   de §7.3. Una excepción no manejada es 500 sin stacktrace interna.
4. CORS — deshabilitado por defecto; se habilita solo si CORS_ORIGINS
   trae orígenes (§11.2: CORS no reemplaza autenticación).

Por qué una fábrica ``crear_app()`` y no una ``app`` global directa: la
fábrica permite construir la aplicación con configuración inyectada (los
tests crean instancias aisladas sin tocar el entorno). ``app`` queda
disponible al final del módulo para que uvicorn la encuentre:

    uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log

Los "middleware" y "handlers" en lenguaje llano: FastAPI procesa cada
petición como una cadena — el middleware corre ANTES y DESPUÉS de la ruta
(envuelve todo, ideal para asignar el request_id), y los exception
handlers son funciones que FastAPI llama cuando una excepción escapa,
para convertir excepciones en respuestas HTTP con la forma del contrato.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextlib import ExitStack, asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.error_i18n import mensaje_localizado
from app.api.openapi_metadata import parametros, respuestas
from app.api.routes import (
    chat,
    demo_documents,
    demo_generations,
    documents,
    exports,
    generations,
    glossary,
    jobs,
    progress,
    workspaces,
)
from app.config import Configuracion, config
from app.jobs.manager import ControlesOperativos, CuotasProveedor, GestorTrabajos
from app.jobs.quotas import cuotas_configuradas
from app.jobs.runtime_lock import exclusividad_runtime
from app.jobs.store import RegistroOperativo
from app.schemas.errors import ErrorAplicacion, ErrorBody, ErrorCode, ErrorResponse
from app.session.manifests import SincronizadorManifiestos, sincronizar_pendientes
from app.session.recovery import restaurar_registro
from app.storage.oci_storage import get_storage_provider
from app.storage.provider import StorageBudgetExceeded, StorageUnavailable

if TYPE_CHECKING:
    from app.core.agents.critic_contracts import VerificadorSync
    from app.core.agents.gemini_generation import GeneradorSync
    from app.core.rag.embeddings import EmbeddingProvider

# ContextVar: una variable que vale "para la petición actual". Los módulos
# de negocio podrán leer el request_id para logging sin pasarlo a mano por
# todas las funciones (§11.5).
# aqui defino una variable de contexto (ContextVar) para aislar el identificador unico de cada peticion (request_id)
# esta herramienta de python permite guardar valores propios de cada flujo asincrono sin mezclar datos entre usuarios
# con esto cumplo el requisito de trazabilidad transversal establecido en la seccion 11.5 de la arquitectura
request_id_actual: ContextVar[str] = ContextVar("request_id", default="sin-request")

logger = logging.getLogger("nuevamente.api")


# con esta clase filtro los registros de error del servidor para no exponer trazas internas (stacktraces)
# en python personalizo la clase Filter del modulo logging para limpiar los mensajes antes de que salgan a consola
# esto responde al requisito de seguridad que prohibe filtrar secretos o rutas del sistema en caso de fallo
class _FiltroErrorServidor(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            record.msg = "Excepcion ASGI tipo=%s"
            record.args = (getattr(record.exc_info[0], "__name__", "desconocido"),)
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


# Cabecera estándar de trazabilidad del proyecto (contratos-api.md:
# "toda respuesta incluye cabecera X-Request-ID").
CABECERA_REQUEST_ID = "X-Request-ID"

# Mapeo HTTP <-> código del contrato para las HTTPException que lancen las
# rutas (§7.3). Las que no estén en la tabla caen al 500 genérico.
_MAPEO_HTTP_A_CODIGO: dict[int, ErrorCode] = {
    400: ErrorCode.INVALID_REQUEST,
    401: ErrorCode.SESSION_INVALID,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.INVALID_STATE,
    413: ErrorCode.DOCUMENT_TOO_LARGE,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
    503: ErrorCode.STORAGE_UNAVAILABLE,
}


# con esta funcion auxiliar armo la estructura estandar de error que exige el contrato de la api
# utilizo el modelo de pydantic para validar los campos y luego lo transformo en un diccionario serializable
# esto asegura que el frontend reciba siempre un formato uniforme con codigo, mensaje y detalles
def _envoltorio(
    codigo: ErrorCode, mensaje: str, request_id: str, detalles: dict | None = None, *, request: Request | None = None
) -> dict:
    """Arma el cuerpo de error del contrato (§7.3) como dict serializable.

    Forma exacta: {"error": {"code", "message", "details"}, "request_id"}.
    Se usa dict en vez del modelo para no filtrar campos de más por accidente.
    """
    return ErrorResponse(
        error=ErrorBody(code=codigo, message=mensaje_localizado(codigo, mensaje, request), details=detalles),
        request_id=request_id,
    ).model_dump()


# aqui busco y recupero el identificador de la peticion actual consultando dos origenes distintos
# primero reviso el estado compartido de starlette (request.state) y si no existe consulto la variable de contexto
# de esta manera garantizo que cualquier manejador de errores pueda incluir el id de seguimiento sin fallar
def _request_id_de(request: Request) -> str:
    """Recupera el request_id de la petición actual para los handlers.

    Dos fuentes, en orden:

    1. ``request.state.request_id`` — lo deja el middleware. Es un espacio
       de datos compartido por TODA la cadena de la petición (misma
       "scope"), así que lo ve incluso el handler de excepciones no
       manejadas, que corre en el middleware MÁS externo de Starlette y
       por fuera del alcance del ContextVar.
    2. El ContextVar como fallback defensivo.
    """
    return getattr(request.state, "request_id", None) or request_id_actual.get()


# con esta funcion creadora (factory pattern) construyo e inicializo la aplicacion de fastapi
# aplicar este patron me permite inyectar configuraciones alternativas para pruebas automatizadas sin tocar variables globales
# aqui valido al inicio las credenciales y limites operativos para evitar que el sistema falle a mitad de una ejecucion
def crear_app(
    configuracion: Configuracion | None = None,
    *,
    proveedor_embeddings: EmbeddingProvider | None = None,
    proveedor_vision: VerificadorSync | None = None,
    proveedor_generacion: GeneradorSync | None = None,
    proveedor_verificacion: VerificadorSync | None = None,
    cuotas_proveedor: CuotasProveedor | None = None,
    proveedor_storage=None,
) -> FastAPI:
    """Fábrica de la aplicación. Recibe configuración inyectable para tests.

    Al construir la app valida la configuración crítica: en producción, si
    falta algo, ``crear_app`` lanza ConfiguracionIncompleta y el proceso no
    llega a abrir el puerto (criterio 3 del issue: fallar al arrancar con
    mensaje accionable, no a mitad de una generación).
    """
    ajustes = configuracion or config
    log_servidor = logging.getLogger("uvicorn.error")
    if not any(isinstance(filtro, _FiltroErrorServidor) for filtro in log_servidor.filters):
        log_servidor.addFilter(_FiltroErrorServidor())
    ajustes.validar_critico()
    if cuotas_proveedor is None:
        cuotas = cuotas_configuradas(ajustes)
        cuotas_proveedor = CuotasProveedor(cuotas) if cuotas else None
    if (proveedor_embeddings is not None or proveedor_vision is not None) and cuotas_proveedor is None:
        raise ValueError("El proveedor documental requiere las cuotas compartidas explícitas")
    if (proveedor_generacion is None) != (proveedor_verificacion is None):
        raise ValueError("Inyectar juntos generación y verificación")

    # defino el administrador de ciclo de vida (lifespan) usando un manejador de contexto asincrono (asynccontextmanager)
    # este mecanismo moderno de fastapi reemplaza a los antiguos eventos de inicio y apagado (startup y shutdown)
    # aqui dentro inicio la base de datos de sqlite, los clientes de almacenamiento en la nube y el gestor de trabajos en cola
    # al finalizar la ejecucion el bloque finally garantiza que todos los recursos y conexiones se liberen ordenadamente
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        gestor = None
        recursos = None
        sincronizador = None
        proveedores_texto = []
        guardia = ExitStack()
        try:
            guardia.enter_context(exclusividad_runtime(Path(ajustes.data_dir)))
            restaurar_registro(app.state.db, app.state.storage_provider)
            sincronizar_pendientes(app.state.db, app.state.storage_provider)
            controles = ControlesOperativos(
                max_en_cola=ajustes.max_queued_jobs,
                deadline_ejecucion_segundos=ajustes.generation_deadline_seconds,
            )
            gestor = GestorTrabajos(app.state.db, controles=controles, cuotas=cuotas_proveedor)
            app.state.jobs_manager = gestor
            if (
                proveedor_embeddings is not None
                and cuotas_proveedor.disponibles_hoy(ajustes.gemini_embedding_model) is None
            ):
                raise ValueError("La integración documental requiere cuotas explícitas para el modelo de embeddings")
            if (
                cuotas_proveedor is not None
                and cuotas_proveedor.disponibles_hoy(ajustes.gemini_embedding_model) is not None
            ):
                from app.documents.runtime import crear_recursos_documentales

                recursos = crear_recursos_documentales(
                    ajustes,
                    app.state.db,
                    app.state.storage_provider,
                    gestor,
                    proveedor=proveedor_embeddings,
                    proveedor_vision=proveedor_vision,
                )
                app.state.vectorstore = recursos.indice
                app.state.documents_admission = recursos.admision
                app.state.documents_query = recursos.consulta
            else:
                from app.documents.query import ServicioConsultaDocumental

                # Listado/detalle no requieren Gemini ni inventan cuotas. El
                # upload y las fuentes con índice no configurado fallan 503.
                app.state.documents_query = ServicioConsultaDocumental(
                    app.state.db,
                    app.state.storage_provider,
                    None,
                    plazo_lectura=ajustes.generation_deadline_seconds,
                )
            gen, ver = proveedor_generacion, proveedor_verificacion
            modelos_listos = cuotas_proveedor is not None and all(
                cuotas_proveedor.disponibles_hoy(m) is not None
                for m in (ajustes.gemini_generation_model, ajustes.gemini_verification_model)
            )
            if gen is not None and (not modelos_listos or app.state.vectorstore is None):
                raise ValueError("Generación requiere índice y cuotas compartidas de ambos modelos")
            if gen is None and modelos_listos and app.state.vectorstore is not None and not ajustes.mock_gemini:
                from app.core.agents.gemini_generation import ClienteGeminiGeneracion

                gen = ver = ClienteGeminiGeneracion(ajustes)
            if gen is not None:
                proveedores_texto = list({id(p): p for p in (gen, ver)}.values())
            from app.chat.service import ServicioChat
            from app.generations.service import ServicioGeneraciones

            # Las lecturas de material ya aprobado no dependen del runtime LLM.
            app.state.generations = ServicioGeneraciones(
                app.state.db, app.state.storage_provider, app.state.documents_query, gestor, ajustes, gen, ver
            )
            app.state.chat = ServicioChat(app.state.db, app.state.documents_query, gestor, ajustes, gen, ver)
            from app.documents.demo import ServicioCatalogoDemo
            from app.documents.lifecycle import EfectosDocumentales
            from app.exports.router import ServicioExportaciones
            from app.generations.catalog import CatalogoGeneraciones
            from app.glossary.service import ServicioGlosario
            from app.progress.service import ServicioProgreso

            app.state.glossary = ServicioGlosario(app.state.db, app.state.documents_query, gestor, ajustes, gen, ver)

            app.state.demo_documents_query = ServicioCatalogoDemo(
                app.state.db,
                app.state.storage_provider,
                app.state.vectorstore,
                plazo=ajustes.generation_deadline_seconds,
            )

            efectos = (
                app.state.documents_admission.procesar.efectos
                if app.state.documents_admission is not None
                else EfectosDocumentales(app.state.db, app.state.storage_provider, None)
            )
            efectos.gestor = gestor
            app.state.documents_effects = efectos
            app.state.generations.exports = ServicioExportaciones(app.state.generations.catalogo, guardia=efectos._lock)
            # La limpieza espera a las escrituras de estudio antes de purgar
            # progreso: un PUT en vuelo no puede recrear referencias retiradas.
            app.state.progress = ServicioProgreso(
                app.state.db,
                app.state.storage_provider,
                CatalogoGeneraciones(
                    app.state.db, app.state.storage_provider, app.state.documents_query, ajustes.oci_bucket_name
                ),
                guardia=efectos._lock,
            )
            efectos.reconciliar()
            from app.session.lifecycle import CicloVidaEspacios

            ciclo = CicloVidaEspacios(app.state.db, app.state.storage_provider, efectos, gestor)
            app.state.workspace_lifecycle = ciclo
            ciclo.reconciliar()

            def mantenimiento():
                ciclo.reconciliar()
                return efectos.reconciliar()

            sincronizador = SincronizadorManifiestos(
                app.state.db, app.state.storage_provider, efectos_documentales=mantenimiento
            )
            sincronizador.iniciar()
            yield
        finally:
            # Ningún finally puede cerrar recursos si stop falló. Se espera al
            # único worker y a observadores externos; no se mata un hilo activo.
            if sincronizador is not None:
                sincronizador.detener()
            if gestor is not None:
                gestor.detener(timeout=None)
            try:
                try:
                    for proveedor in proveedores_texto:
                        cerrar = getattr(proveedor, "cerrar", None)
                        if callable(cerrar):
                            cerrar()
                    if recursos is not None and recursos.vision is not None:
                        cerrar_vision = getattr(recursos.vision.proveedor, "cerrar", None)
                        if callable(cerrar_vision):
                            cerrar_vision()
                finally:
                    if app.state.vectorstore is not None:
                        app.state.vectorstore.cerrar()
            finally:
                app.state.documents_admission = None
                app.state.documents_query = None
                app.state.generations = None
                app.state.chat = None
                app.state.progress = None
                app.state.glossary = None
                app.state.demo_documents_query = None
                app.state.documents_effects = None
                app.state.vectorstore = None
                try:
                    cerrar_storage = getattr(app.state.storage_provider, "cerrar", None)
                    if callable(cerrar_storage):
                        cerrar_storage()
                finally:
                    try:
                        app.state.db.cerrar()
                    finally:
                        guardia.close()

    app = FastAPI(
        title="NuevaMente API",
        description="Adaptación pedagógica de documentos técnicos (Hackathon ONE — Equipo 38).",
        version="0.1.0",
        lifespan=lifespan,
        # La referencia viva de estos contratos es docs/contratos-api.md
        # (congelado v1 por el issue #03); /docs es la vista navegable.
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # ------------------------- Middleware request_id -------------------------

    # este intermediario (middleware) intercepta absolutamente todas las peticiones entrantes y salientes
    # con la llamada await call_next cedo el control para que se ejecute la ruta correspondiente y luego retomo la respuesta
    # aqui asigno o genero un identificador unico (uuid4) para asegurar la trazabilidad de cada peticion en los registros del sistema
    @app.middleware("http")
    async def asignar_request_id(request: Request, call_next):
        """Corre envolviendo TODA petición: asigna/acepta el request_id.

        - Si el cliente trae X-Request-ID (p. ej. el frontend correlacionando
          sus propios logs), se respeta; si no, se genera uno nuevo con uuid4
          (identificador aleatorio estándar de 122 bits: colisiones
          prácticas nulas).
        - El id se guarda en el ContextVar (accesible a todo el código de la
          petición), se agrega como cabecera de respuesta y se loguea junto
          al método y la ruta (§11.5).
        """
        request_id = request.headers.get(CABECERA_REQUEST_ID, "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", request_id):
            request_id = f"req_{uuid.uuid4().hex}"
        # Dos formas de publicarlo para el resto del código:
        # - request.state: visible en TODA la cadena, incluidos los handlers
        #   de excepción que corren fuera de este middleware;
        # - ContextVar: cómodo para logging en módulos sin acceso al request.
        request.state.request_id = request_id
        token = request_id_actual.set(request_id)
        try:
            respuesta = await call_next(request)
        finally:
            # Siempre restaurar el contexto, aun si la ruta explotó.
            request_id_actual.reset(token)
        respuesta.headers[CABECERA_REQUEST_ID] = request_id
        ruta_log = getattr(request.scope.get("route"), "path", "ruta_no_encontrada")
        metodo_log = (
            request.method
            if request.method in {"GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH"}
            else "OTRO"
        )
        logger.info("%s %s request_id=%s", metodo_log, ruta_log, request_id)
        return respuesta

    # ------------------------- CORS opcional (§11.2) -------------------------

    # Solo si la configuración trae orígenes explícitos se instala el
    # middleware: deshabilitado por defecto, como exige el issue #07.
    origenes = ajustes.origenes_cors
    if origenes:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origenes,
            # Sin cookies ni credenciales entre orígenes en este proyecto.
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type", CABECERA_REQUEST_ID, "Idempotency-Key", "Last-Event-ID"],
            expose_headers=[CABECERA_REQUEST_ID, "Retry-After"],
        )

    # ------------------------- Handlers de excepción -------------------------

    # con este decorador capturo las excepciones de almacenamiento cuando el servicio de oracle cloud (oci) no responde
    # transformo el error interno en una respuesta http 503 controlada para que el cliente comprenda la indisponibilidad temporal
    @app.exception_handler(StorageUnavailable)
    async def manejar_storage(request: Request, exc: StorageUnavailable):
        request_id = _request_id_de(request)
        return JSONResponse(
            status_code=503,
            content=_envoltorio(
                ErrorCode.STORAGE_UNAVAILABLE,
                "El almacenamiento no está disponible; revisar OCI y su presupuesto.",
                request_id,
                detalles={"motivo": "storage_budget"} if isinstance(exc, StorageBudgetExceeded) else None,
                request=request,
            ),
            headers={CABECERA_REQUEST_ID: request_id},
        )

    # aqui capturo las excepciones de logica de negocio (ErrorAplicacion) definidas en nuestros esquemas
    # devuelvo el codigo de estado http correspondiente junto con la estructura estandar de error del contrato
    @app.exception_handler(ErrorAplicacion)
    async def manejar_dominio(request: Request, exc: ErrorAplicacion):
        request_id = _request_id_de(request)
        return JSONResponse(
            status_code=exc.status_code,
            content=_envoltorio(exc.error.code, exc.error.message, request_id, exc.error.details, request=request),
            headers={**exc.headers, CABECERA_REQUEST_ID: request_id},
        )

    # aqui capturo los errores de validacion que lanza pydantic cuando el cuerpo de una peticion no coincide con el esquema
    # devuelvo un codigo http 422 y extraigo unicamente la ubicacion y descripcion del fallo sin divulgar datos confidenciales
    @app.exception_handler(RequestValidationError)
    async def manejar_validacion(request: Request, exc: RequestValidationError):
        """Body que no respeta el contrato -> 422 VALIDATION_ERROR.

        Pydantic ya detectó el problema; aquí solo lo envolvemos en la forma
        estándar. Los detalles técnicos (qué campo falló) van en
        error.details para que el frontend pueda marcar el campo exacto.
        """
        request_id = _request_id_de(request)
        return JSONResponse(
            status_code=422,
            content=_envoltorio(
                ErrorCode.VALIDATION_ERROR,
                "La peticion no respeta el contrato del endpoint.",
                request_id,
                # No devolver input (puede contener secretos) ni ctx (incluye excepciones no serializables).
                detalles={"errores": [{k: e[k] for k in ("loc", "msg", "type")} for e in exc.errors()]},
                request=request,
            ),
            headers={CABECERA_REQUEST_ID: request_id},
        )

    @app.exception_handler(StarletteHTTPException)
    async def manejar_http(request: Request, exc: StarletteHTTPException):
        """HTTPException de las rutas -> envoltorio con el código de §7.3."""
        request_id = _request_id_de(request)
        codigo = _MAPEO_HTTP_A_CODIGO.get(exc.status_code, ErrorCode.INTERNAL)
        return JSONResponse(
            status_code=exc.status_code,
            content=_envoltorio(codigo, str(exc.detail), request_id, request=request),
            headers={**(exc.headers or {}), CABECERA_REQUEST_ID: request_id},
        )

    # con este manejador final atrapo cualquier excepcion no controlada para prevenir caidas abruptas del servidor
    # respondo con un estado 500 generico y oculto la traza de ejecucion para proteger la seguridad de la infraestructura
    @app.exception_handler(Exception)
    async def manejar_no_manejada(request: Request, exc: Exception):
        """Cualquier excepción no manejada -> 500 INTERNAL, sin interior.

        Criterio de aceptación del issue: responde con el envoltorio estándar
        y request_id, y NO expone la stacktrace (detalles sensibles, §7.3).
        El log conserva el tipo de excepción y request_id, sin el mensaje
        del proveedor que podría contener documentos o credenciales.
        """
        request_id = _request_id_de(request)
        # Una excepción de proveedor puede incluir prompts, tokens o texto del documento.
        logger.error("Excepcion no manejada tipo=%s request_id=%s", type(exc).__name__, request_id)
        return JSONResponse(
            status_code=500,
            content=_envoltorio(
                ErrorCode.INTERNAL,
                "Error interno del servidor. Reportar el request_id al equipo.",
                request_id,
                request=request,
            ),
            # Este handler corre FUERA del middleware request_id, así que la
            # cabecera de trazabilidad la agrega él mismo.
            headers={CABECERA_REQUEST_ID: request_id},
        )

    # ------------------------------ Rutas -----------------------------------

    # con esta ruta expongo una verificacion basica de salud (health check) requerida para monitoreo
    # respondo de forma rapida y ligera sin consultar servicios externos para evitar falsos negativos en el orquestador
    @app.get("/api/health", responses=respuestas(), openapi_extra=parametros())
    async def health() -> dict:
        """Disponibilidad básica (§7.1): sin secretos, sin llamadas LLM.

        Responde 200 con estado y versión cuando el proceso está vivo. No
        consulta OCI ni Gemini a propósito: health debe ser barato y no
        puede tirar el servicio por una dependencia externa lenta.
        """
        return {"status": "ok", "version": app.version}

    app.state.config = ajustes
    app.state.db = RegistroOperativo(
        ruta_db=Path(ajustes.data_dir) / "operativo.db",
        dias_retencion=ajustes.workspace_retention_days,
        limite_documentos=ajustes.workspace_max_documents,
        limite_generaciones=ajustes.workspace_max_generations,
    )
    app.state.storage_provider = (
        proveedor_storage if proveedor_storage is not None else get_storage_provider(configuracion=ajustes)
    )
    # Los schemas públicos documentales se implementan con la dirección del
    # equipo; sus precisiones conservan revisión contract-change pendiente.
    app.state.documents_admission = None
    app.state.documents_query = None
    app.state.demo_documents_query = None
    app.state.documents_effects = None
    app.state.vectorstore = None
    app.state.generations = None
    app.state.chat = None
    app.state.progress = None
    app.state.glossary = None

    # aqui registro en la aplicacion principal los diferentes enrutadores (routers) divididos por dominio
    # esta modularizacion desacopla los modulos de documentos, generaciones, espacios de trabajo y exportaciones
    app.include_router(workspaces.router)
    app.include_router(jobs.router)
    app.include_router(documents.router)
    app.include_router(demo_documents.router)
    app.include_router(demo_generations.router)
    app.include_router(generations.router)
    app.include_router(exports.router)
    app.include_router(chat.router)
    app.include_router(progress.router)
    app.include_router(glossary.router)

    return app


# Instancia por defecto que uvicorn encuentra con "app.main:app". Los tests
# usan crear_app(configuracion=...) con sus propios ajustes.
app = crear_app()
