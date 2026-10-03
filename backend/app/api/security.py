import threading
from datetime import datetime, timedelta

from fastapi import Request

from app.schemas.errors import ErrorAplicacion, ErrorCode

_lock = threading.RLock()

# Diccionarios generalizados
# Estructura: { "accion": [timestamps] }
_peticiones_globales: dict[str, list[datetime]] = {}
# Estructura: { "accion:ip": [timestamps] }
_peticiones_ip: dict[str, list[datetime]] = {}
# Estructura: { "ip": [timestamps] } (Solo para fallos de recuperación)
_intentos_fallidos_ip: dict[str, list[datetime]] = {}


def _contar_eventos_recientes(registro: list[datetime], ventana_minutos: int = 1) -> int:
    ahora = datetime.now()
    limite_tiempo = ahora - timedelta(minutes=ventana_minutos)
    registro[:] = [t for t in registro if t > limite_tiempo]
    return len(registro)


def _obtener_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# Fábrica de Dependencias para Rate Limiting
class RateLimiter:
    """Dependencia reutilizable para limitar peticiones globales y por IP."""

    def __init__(self, key: str, limite_global: int, limite_ip: int):
        self.key = key
        self.limite_global = limite_global
        self.limite_ip = limite_ip

    def __call__(self, request: Request) -> str:
        ip_cliente = _obtener_ip(request)
        clave_ip = f"{self.key}:{ip_cliente}"

        with _lock:
            # Prevención de Out Of Memory (OOM)
            if len(_peticiones_ip) > 10000:
                _peticiones_ip.clear()

            if len(_peticiones_globales) > 10000:
                _peticiones_globales.clear()

            # Validación Global
            _peticiones_globales.setdefault(self.key, [])
            if _contar_eventos_recientes(_peticiones_globales[self.key]) >= self.limite_global:
                raise ErrorAplicacion(
                    code=ErrorCode.RATE_LIMITED,
                    message="Se ha alcanzado el límite global de solicitudes permitido. Por favor, inténtalo nuevamente más tarde.",
                )

            # Validación por IP
            _peticiones_ip.setdefault(clave_ip, [])
            if _contar_eventos_recientes(_peticiones_ip[clave_ip]) >= self.limite_ip:
                raise ErrorAplicacion(
                    code=ErrorCode.RATE_LIMITED,
                    message="Se ha alcanzado el límite de solicitudes permitidas. Por favor, inténtalo nuevamente más tarde.",
                )

            # Registrar la petición
            ahora = datetime.now()
            _peticiones_globales[self.key].append(ahora)
            _peticiones_ip[clave_ip].append(ahora)

        return ip_cliente


# 2. Bloqueo específico contra Fuerza Bruta (Aplica a errores de negocio)
def validar_intentos_fallidos(request: Request) -> str:
    """Bloquea estrictamente si hay 5 fallos de intento de acceso."""
    ip_cliente = _obtener_ip(request)
    with _lock:
        # Prevención de Out Of Memory (OOM)
        if len(_intentos_fallidos_ip) > 10000:
            _intentos_fallidos_ip.clear()

        _intentos_fallidos_ip.setdefault(ip_cliente, [])

        if _contar_eventos_recientes(_intentos_fallidos_ip[ip_cliente]) >= 5:
            raise ErrorAplicacion(
                code=ErrorCode.RECOVERY_LOCKED,
                message="Se ha alcanzado el límite de intentos fallidos, bloqueo temporal de 1 minuto aplicado.",
            )

    return ip_cliente


def registrar_intento_fallido(ip: str) -> None:
    with _lock:
        _intentos_fallidos_ip.setdefault(ip, []).append(datetime.now())


def limpiar_intentos_fallidos(ip: str) -> None:
    """Limpia los intentos fallidos de una IP tras un canje exitoso."""
    with _lock:
        _intentos_fallidos_ip.pop(ip, None)
