import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from ipaddress import ip_address

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
_intentos_en_curso: dict[str, int] = {}


def _contar_eventos_recientes(registro: list[datetime], ventana_minutos: int = 1) -> int:
    ahora = datetime.now()
    limite_tiempo = ahora - timedelta(minutes=ventana_minutos)
    registro[:] = [t for t in registro if t > limite_tiempo]
    return len(registro)


def _obtener_ip(request: Request) -> str:
    origen = request.headers.get("X-NuevaMente-Client-IP")
    clave = request.headers.get("X-NuevaMente-Origin-Key")
    if origen is not None or clave is not None:
        config = getattr(request.app.state, "config", None)
        secreto = getattr(config, "trusted_origin_secret", "")
        if not secreto or not clave or not secrets.compare_digest(clave.encode("utf-8"), secreto.encode("utf-8")):
            raise ErrorAplicacion(ErrorCode.INVALID_REQUEST, "Origen de cliente no autenticado.")
        try:
            return str(ip_address(origen))
        except (ValueError, TypeError):
            raise ErrorAplicacion(ErrorCode.INVALID_REQUEST, "Origen de cliente inválido.") from None
    # X-Forwarded-For del usuario no cambia el límite.
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

        if _contar_eventos_recientes(_intentos_fallidos_ip[ip_cliente]) + _intentos_en_curso.get(ip_cliente, 0) >= 5:
            raise ErrorAplicacion(
                code=ErrorCode.RECOVERY_LOCKED,
                message="Se ha alcanzado el límite de intentos fallidos, bloqueo temporal de 1 minuto aplicado.",
                headers={"Retry-After": "60"},
            )

    return ip_cliente


def registrar_intento_fallido(ip: str) -> None:
    with _lock:
        _intentos_fallidos_ip.setdefault(ip, []).append(datetime.now())


def limpiar_intentos_fallidos(ip: str) -> None:
    """Reinicialización explícita del registro; un canje no borra fallos previos."""
    with _lock:
        _intentos_fallidos_ip.pop(ip, None)


@contextmanager
def intento_recuperacion(request: Request):
    """Reserva un intento: fallos e intentos en vuelo comparten las cinco plazas."""
    with _lock:
        ip = validar_intentos_fallidos(request)
        _intentos_en_curso[ip] = _intentos_en_curso.get(ip, 0) + 1
    try:
        yield ip
    except ErrorAplicacion as error:
        if error.error.code == ErrorCode.SESSION_INVALID:
            registrar_intento_fallido(ip)
        raise
    finally:
        with _lock:
            _intentos_en_curso[ip] -= 1
            if not _intentos_en_curso[ip]:
                del _intentos_en_curso[ip]
