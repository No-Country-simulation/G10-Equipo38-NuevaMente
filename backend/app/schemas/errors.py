"""Modelo de errores del contrato v1 (issue #03, referencia §7.3).

Todas las respuestas de error de la API comparten la MISMA envoltura:

    {"error": {"code": ..., "message": ..., "details": ...}, "request_id": ...}

Por qué una envoltura única: el frontend (y cualquier consumidor autorizado)
puede mostrar un mensaje humano y reaccionar al código de máquina sin
adivinar la forma de cada endpoint. ``request_id`` permite correlacionar la
respuesta del usuario con las líneas de log del backend (§11.5: los logs
registran request_id, etapa, duración y código de error).

División de responsabilidades (§7.3 y §17.3):

- ``code``: código de MÁQUINA, estable y sin traducir. Es parte del contrato
  congelado: cambiarlo rompe clientes. La tabla de códigos → HTTP está abajo,
  espejo de docs/contratos-api.md.
- ``message``: texto para humanos, se localiza según el idioma de UI
  (Accept-Language). Puede cambiar sin romper el contrato.
- ``details``: datos estructurados opcionales del error (límites recibidos,
  posición en cola...). Claves libres, no parte del contrato congelado.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(str, Enum):
    """Códigos de error estables de toda la API (tabla de §7.3).

    Cada código viaja SIEMPRE con el HTTP que indica la tabla; la asociación
    es parte del contrato congelado v1. Los nombres van en mayúsculas por
    convención de códigos de máquina.
    """

    # 400: la petición no respeta el formato esperado.
    INVALID_REQUEST = "INVALID_REQUEST"
    # 401: token de sesión inválido/vencido o código de recuperación erróneo.
    SESSION_INVALID = "SESSION_INVALID"
    # 404: recurso inexistente O ajeno al espacio (§7: un ID ajeno responde
    # 404 y no 403, para no filtrar la existencia de recursos de otros).
    NOT_FOUND = "NOT_FOUND"
    # 409: Idempotency-Key reutilizada con un cuerpo distinto.
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    # 409: estado incompatible para la operación pedida.
    INVALID_STATE = "INVALID_STATE"
    # 413: archivo o texto plano por encima del límite de tamaño.
    DOCUMENT_TOO_LARGE = "DOCUMENT_TOO_LARGE"
    # 422: combinación inválida de parámetros o formato de exportación
    # incompatible (ej.: exportar CSV de un contenido que no es flashcards).
    EXPORT_INCOMPATIBLE = "EXPORT_INCOMPATIBLE"
    # 422: validación semántica que Pydantic no cubre solo con tipos.
    VALIDATION_ERROR = "VALIDATION_ERROR"
    # 429: la cola de trabajos está llena (§7.5: hasta 5 en espera).
    QUEUE_FULL = "QUEUE_FULL"
    # 429: límite de uso alcanzado (cuota compartida del proveedor).
    RATE_LIMITED = "RATE_LIMITED"
    # 429: demasiados intentos fallidos de recuperar el código (§7.5).
    RECOVERY_LOCKED = "RECOVERY_LOCKED"
    # 500: error interno; el mensaje nunca expone detalles sensibles.
    INTERNAL = "INTERNAL"
    # 503: Object Storage (OCI) no disponible. Regla de §3.3: una falla de
    # OCI es failed/STORAGE_UNAVAILABLE, nunca rejected_quality.
    STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"
    # 503: el proveedor de IA no está disponible.
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


class ErrorBody(BaseModel):
    """El objeto ``error`` de la envoltura. Sus tres claves son el contrato."""

    # ConfigDict configura Pydantic a nivel de clase. "extra='forbid'" ordena
    # RECHAZAR cualquier campo no declarado: si el backend agrega una clave
    # nueva sin pasar por contract-change, el propio modelo lo impide (§7.2
    # manda "rechazo de campos desconocidos").
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode = Field(description="Código de máquina estable; ver ErrorCode.")
    message: str = Field(description="Mensaje humano, localizable; no es contrato estable.")
    details: dict[str, Any] | None = Field(
        default=None,
        description="Datos estructurados opcionales (límites, posición en cola, etc.).",
    )


class ErrorResponse(BaseModel):
    """Forma EXACTA del JSON de error que devuelve toda la API (§7.3).

    Ejemplo real de la tabla de contratos:

        {"error": {"code": "DOCUMENT_TOO_LARGE",
                   "message": "El archivo supera el límite de 20 MB para PDF.",
                   "details": {"limite_mb": 20, "recibido_mb": 25.4}},
         "request_id": "req_8f14e45f"}
    """

    model_config = ConfigDict(extra="forbid")

    error: ErrorBody
    request_id: str = Field(description="Correlaciona la respuesta con los logs (§11.5).")


HTTP_POR_CODIGO = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.SESSION_INVALID: 401,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.DOCUMENT_TOO_LARGE: 413,
    ErrorCode.EXPORT_INCOMPATIBLE: 422,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.QUEUE_FULL: 429,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.RECOVERY_LOCKED: 429,
    ErrorCode.INTERNAL: 500,
    ErrorCode.STORAGE_UNAVAILABLE: 503,
    ErrorCode.PROVIDER_UNAVAILABLE: 503,
}


class ErrorAplicacion(Exception):
    """Error público de dominio; mensaje/detalles seguros, sin texto crudo del proveedor."""

    def __init__(self, code: ErrorCode, message: str, details: dict | None = None):
        super().__init__(message)
        self.error = ErrorBody(code=code, message=message, details=details)
        self.status_code = HTTP_POR_CODIGO[self.error.code]
