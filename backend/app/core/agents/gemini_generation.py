"""Adaptador síncrono de google-genai; ctx.llamar es el único dueño de retries."""

import json
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx
from google import genai
from google.genai import errors, types

from app.config import Configuracion
from app.jobs.manager import CuotaAgotadaError, ReintentableError


class GeneracionGeminiError(RuntimeError):
    """Fallo permanente o respuesta vacía; mensajes sin payloads ni credenciales."""


class GeneradorSync(Protocol):
    es_mock: bool

    def generar_sync(
        self,
        prompt: str,
        *,
        modelo: str,
        system_instruction: str,
        response_json_schema: dict,
        timeout: float,
        max_output_tokens: int,
    ) -> str: ...


def _retry_after(error: errors.APIError) -> float | None:
    valor = error.response.headers.get("Retry-After") if error.response is not None else None
    if valor is None:
        detalles = error.details.get("error", {}).get("details", []) if isinstance(error.details, dict) else []
        for detalle in detalles if isinstance(detalles, list) else []:
            if isinstance(detalle, dict) and detalle.get("@type", "").endswith("RetryInfo"):
                delay = detalle.get("retryDelay")
                if isinstance(delay, str) and delay.endswith("s"):
                    try:
                        segundos = float(delay[:-1])
                        if math.isfinite(segundos):
                            return max(0.0, segundos)
                    except ValueError:
                        pass
        return None
    try:
        segundos = float(valor)
        return max(0.0, segundos) if math.isfinite(segundos) else None
    except ValueError:
        try:
            fecha = parsedate_to_datetime(valor)
            if fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=timezone.utc)
            return max(0.0, (fecha - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


def _cuota_diaria(error: errors.APIError) -> bool:
    # Se identifica la ventana a partir de QuotaFailure, no de un 429 genérico.
    detalles = error.details.get("error", {}).get("details", []) if isinstance(error.details, dict) else []
    for detalle in detalles if isinstance(detalles, list) else []:
        if isinstance(detalle, dict) and detalle.get("@type", "").endswith("QuotaFailure"):
            violaciones = detalle.get("violations", [])
            for violacion in violaciones if isinstance(violaciones, list) else []:
                marcador = json.dumps(violacion).lower()
                if any(ventana in marcador for ventana in ("perday", "per_day", "daily", "rpd")):
                    return True
    return False


class ClienteGeminiGeneracion:
    es_mock = False

    def __init__(self, configuracion: Configuracion, *, cliente: genai.Client | None = None) -> None:
        if configuracion.mock_gemini:
            raise ValueError("MOCK_GEMINI requiere un proveedor de pruebas explícito")
        if not configuracion.google_api_key.strip() or configuracion.google_api_key == "placeholder":
            raise ValueError("Configurar GOOGLE_API_KEY para usar Gemini real")
        self._cliente = cliente or genai.Client(
            enterprise=False,
            vertexai=False,
            api_key=configuracion.google_api_key,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
        )

    def cerrar(self) -> None:
        self._cliente.close()

    def generar_sync(
        self,
        prompt: str,
        *,
        modelo: str,
        system_instruction: str,
        response_json_schema: dict,
        timeout: float,
        max_output_tokens: int,
    ) -> str:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout debe ser positivo y finito")
        try:
            respuesta = self._cliente.models.generate_content(
                model=modelo,
                contents=prompt,
                config=types.GenerateContentConfig(
                    candidate_count=1,
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_json_schema=response_json_schema,
                    max_output_tokens=max_output_tokens,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    http_options=types.HttpOptions(
                        timeout=max(1, int(timeout * 1000)), retry_options=types.HttpRetryOptions(attempts=1)
                    ),
                ),
            )
        except errors.APIError as error:
            if error.code == 429 and _cuota_diaria(error):
                raise CuotaAgotadaError("Gemini agotó la cuota diaria del modelo") from None
            if error.code in (408, 429, 500, 502, 503, 504):
                raise ReintentableError(
                    "Gemini no está disponible temporalmente", retry_after=_retry_after(error)
                ) from None
            raise GeneracionGeminiError("Gemini rechazó la solicitud; revisar configuración y modelo") from None
        except httpx.TransportError:
            raise ReintentableError("La solicitud Gemini falló por transporte o timeout") from None
        if any(candidato.finish_reason != types.FinishReason.STOP for candidato in respuesta.candidates or []):
            raise GeneracionGeminiError("Gemini no terminó una respuesta completa")
        texto = respuesta.text
        if not isinstance(texto, str) or not texto.strip():
            raise GeneracionGeminiError("Gemini no devolvió contenido textual")
        return texto
