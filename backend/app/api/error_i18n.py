"""Localización de mensajes públicos. Los códigos y detalles conservan su contrato."""

from fastapi import Request

from app.schemas.errors import ErrorCode

MENSAJES = {
    "en": {
        "INVALID_REQUEST": "The request does not have the expected format.",
        "SESSION_INVALID": "Your session expired or is invalid: recover your space with your code.",
        "NOT_FOUND": "We could not find what you are looking for.",
        "IDEMPOTENCY_CONFLICT": "This operation was already registered with different data.",
        "INVALID_STATE": "The operation does not apply to the current state of the resource.",
        "DOCUMENT_TOO_LARGE": "The document exceeds the allowed size.",
        "EXPORT_INCOMPATIBLE": "That export format does not apply to this material.",
        "VALIDATION_ERROR": "Some form field has an invalid value.",
        "QUEUE_FULL": "The queue is full: try again in a few minutes.",
        "RATE_LIMITED": "You hit the usage limit: please wait a moment.",
        "RECOVERY_LOCKED": "Too many recovery attempts: wait one minute.",
        "INTERNAL": "Internal error. If it persists, report the request identifier.",
        "STORAGE_UNAVAILABLE": "Storage is unavailable right now.",
        "PROVIDER_UNAVAILABLE": "The AI provider is unavailable right now.",
        "SERVICE_UNAVAILABLE": "The server is unavailable at the moment. Please check your connection or try again later.",
        "INVALID_RESPONSE": "The server returned unexpected data. Try again; if it persists, share the request identifier.",
    },
    "pt": {
        "INVALID_REQUEST": "A requisição não está no formato esperado.",
        "SESSION_INVALID": "Sua sessão expirou ou é inválida: recupere o espaço com seu código.",
        "NOT_FOUND": "Não encontramos o que você procura.",
        "IDEMPOTENCY_CONFLICT": "Esta operação já foi registrada com outros dados.",
        "INVALID_STATE": "A operação não se aplica ao estado atual do recurso.",
        "DOCUMENT_TOO_LARGE": "O documento excede o tamanho permitido.",
        "EXPORT_INCOMPATIBLE": "Este formato de exportação não se aplica a este material.",
        "VALIDATION_ERROR": "Algum campo do formulário tem um valor inválido.",
        "QUEUE_FULL": "A fila está cheia: tente novamente em alguns minutos.",
        "RATE_LIMITED": "Você atingiu o limite de uso: aguarde um momento.",
        "RECOVERY_LOCKED": "Muitas tentativas de recuperação: aguarde um minuto.",
        "INTERNAL": "Erro interno. Se persistir, informe o identificador da requisição.",
        "STORAGE_UNAVAILABLE": "O armazenamento está indisponível agora.",
        "PROVIDER_UNAVAILABLE": "O provedor de IA está indisponível agora.",
        "SERVICE_UNAVAILABLE": "O servidor está indisponível no momento. Verifique sua conexão ou tente novamente mais tarde.",
        "INVALID_RESPONSE": "O servidor respondeu com dados inesperados. Tente novamente; se persistir, compartilhe o identificador da solicitação.",
    },
}


def idioma_ui(request: Request) -> str:
    opciones = []
    for orden, parte in enumerate(request.headers.get("Accept-Language", "es")[:256].split(",")):
        etiqueta, *parametros = parte.strip().lower().split(";")
        base = etiqueta.split("-")[0]
        if base not in ("es", "en", "pt", "*"):
            continue
        calidad = 1.0
        try:
            for parametro in parametros:
                if parametro.strip().startswith("q="):
                    calidad = float(parametro.strip()[2:])
        except ValueError:
            continue
        if 0 < calidad <= 1:
            opciones.append((calidad, -orden, "es" if base == "*" else base))
    return max(opciones)[2] if opciones else "es"


def mensaje_localizado(code: ErrorCode, original: str, request: Request | None) -> str:
    idioma = idioma_ui(request) if request is not None else "es"
    return original if idioma == "es" else MENSAJES[idioma].get(code.value, MENSAJES[idioma]["INTERNAL"])
