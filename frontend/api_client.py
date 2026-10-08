import logging
import os
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

API_URL = os.getenv("API_URL", "http://localhost:8000")
DEFAULT_TIMEOUT = (5.0, 30.0)


class APIError(Exception):
    """Excepción personalizada que desempaqueta el envoltorio estándar de error del backend."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        request_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.details = details or {}
        super().__init__(f"[{code}] {message} (request_id: {request_id})")


class APIClient:
    """Cliente HTTP dedicado para consumir la API de FastAPI (contratos v1)."""

    def __init__(self, base_url: str = API_URL, timeout: tuple[float, float] = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self, token: Optional[str] = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
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
    ) -> Any:
        """Ejecuta peticiones HTTP capturando fallos de red/conexión y deserializando errores."""
        url = f"{self.base_url}{endpoint}"
        try:
            res = requests.request(
                method=method,
                url=url,
                json=json,
                params=params,
                headers=self._headers(token),
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as e:
            logger.error("Error de conexión al backend en %s: %s", endpoint, str(e))
            raise APIError(
                code="SERVICE_UNAVAILABLE",
                message="No se pudo establecer conexión con el servidor backend.",
                status_code=503,
            ) from e

        request_id = res.headers.get("X-Request-ID")

        if res.status_code == 204:
            return None

        try:
            data = res.json()
        except ValueError:
            data = {}

        if not res.ok:
            error_payload = data.get("error", {})
            err_code = error_payload.get("code", "UNKNOWN_ERROR")
            err_message = error_payload.get("message", "Error no especificado en el servidor.")
            err_details = error_payload.get("details", {})
            req_id = data.get("request_id") or request_id

            logger.error(
                "API Request falló | HTTP %d | Code: %s | Request-ID: %s | Msg: %s",
                res.status_code,
                err_code,
                req_id,
                err_message,
            )

            raise APIError(
                code=err_code,
                message=err_message,
                status_code=res.status_code,
                request_id=req_id,
                details=err_details,
            )

        if request_id:
            logger.debug("API Request exitosa | HTTP %d | Request-ID: %s", res.status_code, request_id)

        return data

    # ------------------------------------------------------------------
    # 1. Workspaces y Sesiones
    # ------------------------------------------------------------------
    def create_workspace(self) -> dict[str, Any]:
        """POST /api/workspaces -> {workspace_id, recovery_code, token}"""
        return self._solicitar("POST", "/api/workspaces")

    def recover_session(self, recovery_code: str) -> dict[str, Any]:
        """POST /api/sessions/recover -> {workspace_id, token}"""
        return self._solicitar("POST", "/api/sessions/recover", json={"recovery_code": recovery_code})

    def close_session(self, token: str) -> None:
        """DELETE /api/sessions/current"""
        self._solicitar("DELETE", "/api/sessions/current", token=token)

    def rotate_recovery_code(self, token: str) -> dict[str, Any]:
        """POST /api/workspaces/current/recovery-code -> {recovery_code, token}"""
        return self._solicitar("POST", "/api/workspaces/current/recovery-code", token=token)

    def delete_workspace(self, token: str) -> None:
        """DELETE /api/workspaces/current"""
        self._solicitar("DELETE", "/api/workspaces/current", token=token)

    # ------------------------------------------------------------------
    # 2. Documentos
    # ------------------------------------------------------------------
    def upload_document(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/documents/upload"""
        return self._solicitar("POST", "/api/documents/upload", json=payload, token=token)

    def get_documents(self, token: str) -> list[dict[str, Any]]:
        """GET /api/documents"""
        return self._solicitar("GET", "/api/documents", token=token)

    def get_document(self, token: str, document_id: str) -> dict[str, Any]:
        """GET /api/documents/{id}"""
        return self._solicitar("GET", f"/api/documents/{document_id}", token=token)

    def delete_document(self, token: str, document_id: str) -> None:
        """DELETE /api/documents/{id}"""
        self._solicitar("DELETE", f"/api/documents/{document_id}", token=token)

    # ------------------------------------------------------------------
    # 3. Generaciones
    # ------------------------------------------------------------------
    def create_generation(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/generate"""
        return self._solicitar("POST", "/api/generate", json=payload, token=token)

    def get_generations(self, token: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """GET /api/generations"""
        return self._solicitar("GET", "/api/generations", token=token, params=params)

    def get_generation(self, token: str, generation_id: str) -> dict[str, Any]:
        """GET /api/generations/{id}"""
        return self._solicitar("GET", f"/api/generations/{generation_id}", token=token)

    def cancel_generation(self, token: str, generation_id: str) -> None:
        """POST /api/generations/{id}/cancel"""
        self._solicitar("POST", f"/api/generations/{generation_id}/cancel", token=token)

    # ------------------------------------------------------------------
    # 4. Exports
    # ------------------------------------------------------------------
    def get_export_url(self, generation_id: str, fmt: str) -> str:
        """Construye la URL del endpoint de exportación."""
        return f"{self.base_url}/api/exports/{generation_id}?format={fmt}"
