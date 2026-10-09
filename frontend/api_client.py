import logging
import os
import re
from typing import Any, BinaryIO, Literal, Optional, TypedDict, cast
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

API_URL = os.getenv("API_URL", "http://localhost:8000")
DEFAULT_TIMEOUT = (5.0, 30.0)


class SessionResponse(TypedDict):
    workspace_id: str
    token: str


class WorkspaceResponse(SessionResponse):
    recovery_code: str


class RotatedCodeResponse(TypedDict):
    recovery_code: str
    token: str


class TextDocumentRequest(TypedDict):
    documento_titulo: str
    documento_contenido: str


class UploadResponse(TypedDict):
    document_id: str
    status: Literal["processing"]


ExportFormat = Literal["json", "md", "pdf", "csv", "tsv", "apkg"]


def _identificador_seguro(value: object) -> str | None:
    """Solo identificadores breves, sin saltos de línea, en logs y detalles."""
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) else None


class APIError(Exception):
    """Excepción personalizada que desempaqueta el envoltorio estándar de error del backend."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        request_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        retry_after: str | None = None,
    ):
        self.code = code if re.fullmatch(r"[A-Z_]{1,64}", code) else "INTERNAL"
        self.message = message
        self.status_code = status_code
        self.request_id = _identificador_seguro(request_id)
        self.details = details or {}
        self.retry_after = retry_after
        # No incluir mensajes remotos potencialmente privados en tracebacks.
        super().__init__(f"[{self.code}] HTTP {status_code} (request_id: {self.request_id})")


class APIClient:
    """Cliente HTTP dedicado para consumir la API de FastAPI (contratos v1)."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        *,
        idioma_ui: str = "es",
        origen_headers: dict[str, str] | None = None,
    ):
        self.base_url = (base_url or os.getenv("API_URL", API_URL)).rstrip("/")
        self.timeout = timeout
        self.idioma_ui = idioma_ui if idioma_ui in ("es", "en", "pt") else "es"
        self.origen_headers = {
            k: v
            for k, v in (origen_headers or {}).items()
            if k in ("X-NuevaMente-Client-IP", "X-NuevaMente-Origin-Key")
        }

    def _headers(self, token: Optional[str] = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept-Language": self.idioma_ui, **self.origen_headers}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _solicitar(
        self,
        method: str,
        endpoint: str,
        json: Any = None,
        token: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        *,
        files: dict[str, tuple[str, BinaryIO | bytes, str]] | None = None,
        fields: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        binary: bool = False,
    ) -> Any:
        """Ejecuta peticiones HTTP capturando fallos de red/conexión y deserializando errores."""
        url = f"{self.base_url}{endpoint}"
        headers = self._headers(token)
        if files:
            # Requests genera el boundary multipart junto con Content-Type.
            headers.pop("Content-Type")
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        opciones = {"files": files, "data": fields} if files else {}
        try:
            res = requests.request(
                method=method,
                url=url,
                json=json,
                params=params,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
                **opciones,
            )
        except requests.exceptions.RequestException:
            logger.error("Fallo de transporte HTTP al backend")
            raise APIError("SERVICE_UNAVAILABLE", "No se pudo conectar al backend.", 503) from None

        request_id = _identificador_seguro(res.headers.get("X-Request-ID"))
        if 300 <= res.status_code < 400:
            # No reenviar códigos de recuperación ni tokens mediante redirects.
            raise APIError("INVALID_RESPONSE", "Redirección inesperada de la API.", 502, request_id)

        if res.status_code == 204:
            return None

        if binary and res.ok:
            return res.content

        try:
            data = res.json()
        except ValueError:
            if res.ok:
                raise APIError("INVALID_RESPONSE", "Respuesta JSON inválida.", 502, request_id) from None
            data = {}
        if res.ok and not isinstance(data, dict):
            raise APIError("INVALID_RESPONSE", "Se esperaba un objeto JSON.", 502, request_id)

        if not res.ok:
            error_payload = data.get("error") if isinstance(data, dict) else None
            error_payload = error_payload if isinstance(error_payload, dict) else {}
            fallback = "SESSION_INVALID" if res.status_code == 401 else "INTERNAL"
            err_code = error_payload.get("code", fallback)
            err_message = error_payload.get("message", "Error de API.")
            err_details = error_payload.get("details", {})
            req_id = _identificador_seguro(data.get("request_id")) if isinstance(data, dict) else None
            error = APIError(
                code=err_code if isinstance(err_code, str) else fallback,
                message=err_message if isinstance(err_message, str) else "Error de API.",
                status_code=res.status_code,
                request_id=req_id or request_id,
                details=err_details if isinstance(err_details, dict) else {},
                retry_after=res.headers.get("Retry-After"),
            )
            logger.warning("API HTTP %d | Code: %s | Request-ID: %s", res.status_code, error.code, error.request_id)
            raise error

        if request_id:
            logger.debug("API Request exitosa | HTTP %d | Request-ID: %s", res.status_code, request_id)

        return data

    # ------------------------------------------------------------------
    # 1. Workspaces y Sesiones
    # ------------------------------------------------------------------
    @staticmethod
    def _validar_sesion(data: Any, campos: tuple[str, ...]) -> dict[str, str]:
        if not isinstance(data, dict) or any(not isinstance(data.get(c), str) or not data[c] for c in campos):
            raise APIError("INVALID_RESPONSE", "Respuesta de sesión incompleta.", 502)
        return data

    def create_workspace(self) -> WorkspaceResponse:
        """POST /api/workspaces -> {workspace_id, recovery_code, token}"""
        data = self._solicitar("POST", "/api/workspaces")
        return cast(WorkspaceResponse, self._validar_sesion(data, ("workspace_id", "token", "recovery_code")))

    def recover_session(self, recovery_code: str) -> SessionResponse:
        """POST /api/sessions/recover -> {workspace_id, token}"""
        data = self._solicitar("POST", "/api/sessions/recover", json={"recovery_code": recovery_code})
        return cast(SessionResponse, self._validar_sesion(data, ("workspace_id", "token")))

    def close_session(self, token: str) -> None:
        """DELETE /api/sessions/current"""
        self._solicitar("DELETE", "/api/sessions/current", token=token)

    def rotate_recovery_code(self, token: str) -> RotatedCodeResponse:
        """POST /api/workspaces/current/recovery-code -> {recovery_code, token}"""
        data = self._solicitar("POST", "/api/workspaces/current/recovery-code", token=token)
        return cast(RotatedCodeResponse, self._validar_sesion(data, ("token", "recovery_code")))

    def delete_workspace(self, token: str) -> None:
        """DELETE /api/workspaces/current"""
        self._solicitar("DELETE", "/api/workspaces/current", token=token)

    # ------------------------------------------------------------------
    # 2. Documentos
    # ------------------------------------------------------------------
    def upload_document(
        self, token: str, payload: TextDocumentRequest, *, idempotency_key: str | None = None
    ) -> UploadResponse:
        """POST /api/documents/upload"""
        return self._solicitar(
            "POST", "/api/documents/upload", json=payload, token=token, idempotency_key=idempotency_key
        )

    def upload_file(
        self,
        token: str,
        filename: str,
        content: BinaryIO | bytes,
        media_type: str,
        *,
        title: str | None = None,
        idempotency_key: str | None = None,
    ) -> UploadResponse:
        """Sube PDF/MD/TXT por multipart sin alterar el archivo original."""
        return self._solicitar(
            "POST",
            "/api/documents/upload",
            token=token,
            files={"file": (filename, content, media_type)},
            fields={"documento_titulo": title} if title is not None else None,
            idempotency_key=idempotency_key,
        )

    def get_documents(self, token: str, params: dict[str, object] | None = None) -> dict[str, object]:
        """GET /api/documents"""
        return self._solicitar("GET", "/api/documents", token=token, params=params)

    def get_document(self, token: str, document_id: str) -> dict[str, object]:
        """GET /api/documents/{id}"""
        return self._solicitar("GET", f"/api/documents/{quote(document_id, safe='')}", token=token)

    def delete_document(self, token: str, document_id: str) -> None:
        """DELETE /api/documents/{id}"""
        self._solicitar("DELETE", f"/api/documents/{quote(document_id, safe='')}", token=token)

    # ------------------------------------------------------------------
    # 3. Generaciones
    # ------------------------------------------------------------------
    def create_generation(
        self, token: str, payload: dict[str, object], *, idempotency_key: str | None = None
    ) -> dict[str, object]:
        """POST /api/generate"""
        return self._solicitar("POST", "/api/generate", json=payload, token=token, idempotency_key=idempotency_key)

    def get_generations(self, token: str, params: dict[str, object] | None = None) -> dict[str, object]:
        """GET /api/generations"""
        return self._solicitar("GET", "/api/generations", token=token, params=params)

    def get_generation(self, token: str, generation_id: str) -> dict[str, object]:
        """GET /api/generations/{id}"""
        return self._solicitar("GET", f"/api/generations/{quote(generation_id, safe='')}", token=token)

    def cancel_generation(self, token: str, generation_id: str) -> None:
        """POST /api/generations/{id}/cancel"""
        self._solicitar("POST", f"/api/generations/{quote(generation_id, safe='')}/cancel", token=token)

    # ------------------------------------------------------------------
    # 4. Exports
    # ------------------------------------------------------------------
    def download_export(self, token: str, generation_id: str, fmt: ExportFormat) -> bytes:
        """Descarga autorizada desde el servidor; el token nunca va en una URL."""
        return self._solicitar(
            "GET",
            f"/api/exports/{quote(generation_id, safe='')}",
            token=token,
            params={"format": fmt},
            binary=True,
        )
