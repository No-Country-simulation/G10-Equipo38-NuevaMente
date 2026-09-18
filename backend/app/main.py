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

    uvicorn app.main:app --host 0.0.0.0 --port 8000

Los "middleware" y "handlers" en lenguaje llano: FastAPI procesa cada
petición como una cadena — el middleware corre ANTES y DESPUÉS de la ruta
(envuelve todo, ideal para asignar el request_id), y los exception
handlers son funciones que FastAPI llama cuando una excepción escapa,
para convertir excepciones en respuestas HTTP con la forma del contrato.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Configuracion, config
from app.schemas.errors import ErrorBody, ErrorCode, ErrorResponse

# ContextVar: una variable que vale "para la petición actual". Los módulos
# de negocio podrán leer el request_id para logging sin pasarlo a mano por
# todas las funciones (§11.5).
request_id_actual: ContextVar[str] = ContextVar("request_id", default="sin-request")

logger = logging.getLogger("nuevamente.api")

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


def _envoltorio(codigo: ErrorCode, mensaje: str, request_id: str, detalles: dict | None = None) -> dict:
    """Arma el cuerpo de error del contrato (§7.3) como dict serializable.

    Forma exacta: {"error": {"code", "message", "details"}, "request_id"}.
    Se usa dict en vez del modelo para no filtrar campos de más por accidente.
    """
    return ErrorResponse(
        error=ErrorBody(code=codigo, message=mensaje, details=detalles),
        request_id=request_id,
    ).model_dump()


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


def crear_app(configuracion: Configuracion | None = None) -> FastAPI:
    """Fábrica de la aplicación. Recibe configuración inyectable para tests.

    Al construir la app valida la configuración crítica: en producción, si
    falta algo, ``crear_app`` lanza ConfiguracionIncompleta y el proceso no
    llega a abrir el puerto (criterio 3 del issue: fallar al arrancar con
    mensaje accionable, no a mitad de una generación).
    """
    ajustes = configuracion or config
    ajustes.validar_critico()

    app = FastAPI(
        title="NuevaMente API",
        description="Adaptación pedagógica de documentos técnicos (Hackathon ONE — Equipo 38).",
        version="0.1.0",
        # La referencia viva de estos contratos es docs/contratos-api.md
        # (congelado v1 por el issue #03); /docs es la vista navegable.
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # ------------------------- Middleware request_id -------------------------

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
        request_id = request.headers.get(CABECERA_REQUEST_ID) or f"req_{uuid.uuid4().hex[:12]}"
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
        logger.info("%s %s request_id=%s", request.method, request.url.path, request_id)
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
            allow_headers=["Authorization", "Content-Type", CABECERA_REQUEST_ID],
        )

    # ------------------------- Handlers de excepción -------------------------

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
                detalles={"errores": exc.errors()},
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
            content=_envoltorio(codigo, str(exc.detail), request_id),
            headers={CABECERA_REQUEST_ID: request_id},
        )

    @app.exception_handler(Exception)
    async def manejar_no_manejada(request: Request, exc: Exception):
        """Cualquier excepción no manejada -> 500 INTERNAL, sin interior.

        Criterio de aceptación del issue: responde con el envoltorio estándar
        y request_id, y NO expone la stacktrace (detalles sensibles, §7.3).
        El detalle técnico queda SOLO en el log del servidor, correlacionado
        por el mismo request_id que ve el cliente.
        """
        request_id = _request_id_de(request)
        logger.exception("Excepcion no manejada request_id=%s", request_id)
        return JSONResponse(
            status_code=500,
            content=_envoltorio(
                ErrorCode.INTERNAL,
                "Error interno del servidor. Reportar el request_id al equipo.",
                request_id,
            ),
            # Este handler corre FUERA del middleware request_id, así que la
            # cabecera de trazabilidad la agrega él mismo.
            headers={CABECERA_REQUEST_ID: request_id},
        )

    # ------------------------------ Rutas -----------------------------------

    @app.get("/api/health")
    async def health() -> dict:
        """Disponibilidad básica (§7.1): sin secretos, sin llamadas LLM.

        Responde 200 con estado y versión cuando el proceso está vivo. No
        consulta OCI ni Gemini a propósito: health debe ser barato y no
        puede tirar el servicio por una dependencia externa lenta.
        """
        return {"status": "ok", "version": app.version}

    return app


# Instancia por defecto que uvicorn encuentra con "app.main:app". Los tests
# usan crear_app(configuracion=...) con sus propios ajustes.
app = crear_app()
