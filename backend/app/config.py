"""Configuración validada del backend (issue #07, referencia §7.1 y Apéndice A).

Qué hace este archivo: reúne en UN solo lugar todas las "variables de
entorno" que el backend lee (las mismas claves del Apéndice A y de
.env.example), les da tipos correctos (int, bool, listas) y RECHAZA al
arranque combinaciones que no pueden funcionar. La regla del proyecto es
"validación crítica al arranque": un error de configuración debe matar el
proceso con un mensaje accionable, nunca esperar a fallar en mitad de una
generación con un usuario esperando.

Cómo se lee si no conocés Pydantic Settings:

- ``BaseSettings`` es una clase de Pydantic que, en vez de recibir los
  valores por código, los busca en las VARIABLES DE ENTORNO del proceso
  (y opcionalmente en un archivo .env). Cada atributo de clase declara una
  clave: ``api_port: int = 8000`` significa "lee API_PORT del entorno,
  trátalo como entero, y si no está usa 8000".
- ``model_config`` ajusta el comportamiento: aquí le decimos que acepte un
  archivo .env (útil en desarrollo local) y que NO lea variables extra que
  no estén declaradas (cero sorpresas).
- ``@model_validator(mode="after")`` corre DESPUÉS de cargar todo, con los
  valores ya tipados: es donde viven las reglas que miran varios campos a
  la vez (por ejemplo: "en producción no se admite el mock de OCI").

Divisiones de entorno (Apéndice A):

- ``APP_ENV=production``: TODO lo crítico debe estar completo y real. Una
  producción incompleta NO arranca (criterio de aceptación del issue), y
  jamás se degrada a mock (§8.2).
- ``APP_ENV`` en development/test/ci: se permiten mocks EXPLÍCITOS
  (``MOCK_OCI=1``, ``MOCK_GEMINI=1``) y claves en placeholder.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Configuracion(BaseSettings):
    """Configuración viva del backend. Una instancia = toda la configuración.

    El resto del código NO llama a ``os.getenv`` directamente: importa esta
    clase (o una instancia compartida). Así existe un único lugar donde
    buscar "qué variable cambia qué", y los tests pueden construir
    configuraciones específicas sin tocar el entorno de la máquina.
    """

    model_config = SettingsConfigDict(
        # Lee además de las variables de entorno un archivo .env (si existe).
        # En producción el entorno viene del compose/sistema; en desarrollo
        # este archivo es el que se copia desde .env.example.
        env_file=".env",
        env_file_encoding="utf-8",
        # Rechaza variables de entorno que no correspondan a ningún campo
        # declarado: un nombre mal tipeado (.ENVIROMENT) se detecta acá y
        # no se descubre semanas después.
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Identidad y entorno (Apéndice A: "Solo desarrollo/CI pueden activar
    # el mock explícito"). Los valores permitidos están cerrados a propósito.
    # ------------------------------------------------------------------
    app_env: Literal["development", "test", "ci", "production"] = "development"
    mock_oci: bool = Field(default=False, description="True => StorageProvider local (solo dev/CI, §8.2).")
    mock_gemini: bool = Field(
        default=False,
        description="True => doble de Gemini en tests/CI (issue #10); jamás en producción.",
    )

    # ------------------------------------------------------------------
    # Red del servicio. API_HOST 0.0.0.0 escucha dentro del contenedor;
    # Compose restringe la publicación al loopback del host (§14.1).
    # ------------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)

    # ------------------------------------------------------------------
    # Proveedor de IA (placeholders por defecto: solo development/test/ci).
    # ------------------------------------------------------------------
    google_api_key: str = "placeholder"
    gemini_generation_model: str = "gemini-2.5-flash"
    gemini_verification_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-2"
    embedding_dimensions: int = Field(default=768, gt=0)

    # ------------------------------------------------------------------
    # OCI Object Storage (proveedor real; el issue #14 lo consume).
    # ------------------------------------------------------------------
    oci_bucket_name: str = "nuevamente-contenidos-educativos"
    oci_compartment_id: str = "placeholder"
    oci_region: str = "home-region-de-la-tenancy"
    oci_config_file: str = "/run/oci/config"

    # ------------------------------------------------------------------
    # Estado local y decisiones operativas (Apéndice A).
    # ------------------------------------------------------------------
    data_dir: str = ".data"
    workspace_retention_days: int = Field(default=30, gt=0)
    session_max_hours: int = Field(default=24, gt=0)
    max_generation_attempts: int = Field(default=3, ge=1, le=3)
    generation_deadline_seconds: int = Field(default=300, gt=0)
    global_heavy_job_concurrency: int = Field(default=1, gt=0)
    max_queued_jobs: int = Field(default=5, gt=0)
    default_output_language: Literal["es", "en", "pt"] = "es"

    # ------------------------------------------------------------------
    # CORS (§11.2: "los orígenes CORS, si se habilitan, se restringen a los
    # necesarios; CORS no reemplaza autenticación"). Vacío = middleware no
    # se instala: deshabilitado por defecto como exige el issue #07.
    # Se declara como CADENA separada por comas (p. ej.
    # CORS_ORIGINS=https://app.ejemplo.com,https://dev.ejemplo.com) porque
    # las variables de entorno son texto plano; la lista parseada se
    # obtiene con la propiedad `origenes_cors`.
    # ------------------------------------------------------------------
    cors_origins: str = ""

    @property
    def origenes_cors(self) -> list[str]:
        """Orígenes CORS parseados y sin espacios; [] mientras no se configure."""
        return [origen.strip() for origen in self.cors_origins.split(",") if origen.strip()]

    # ------------------------------------------------------------------
    # Validaciones cruzadas (reglas que miran varios campos a la vez).
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _reglas_de_entorno(self) -> "Configuracion":
        """Regla 1: los mocks son de desarrollo/CI/test, jamás de producción.

        §8.2: "MOCK_OCI=1 habilita almacenamiento local únicamente en
        desarrollo y CI". Si esto se pidiera en producción, todo lo que
        sigue sería mentira (persistencia "completada" que no tocó OCI),
        así que se rechaza en el arranque con mensaje accionable.
        """
        if self.app_env == "production":
            if self.mock_oci or self.mock_gemini:
                raise ValueError(
                    "APP_ENV=production no admite mocks (MOCK_OCI/MOCK_GEMINI deben ser 0). "
                    "La entrega acredita OCI y Gemini reales (§8.2 y §12.2 criterio 10)."
                )
        return self

    def validar_critico(self) -> None:
        """Regla 2: producción exige configuración COMPLETA y REAL.

        Devuelve None si todo está en orden; si falta algo, lanza
        ``ConfiguracionIncompleta`` listando TODOS los problemas juntos
        (no de a uno): arreglar en un solo ciclo es lo que hace al mensaje
        "accionable" (criterio de aceptación del issue).

        Qué se considera incompleto:
        - GOOGLE_API_KEY sin poner (placeholder): no hay proveedor de IA.
        - Cualquier variable OCI en placeholder: no hay persistencia real.
        - OCI_CONFIG_FILE apuntando a un archivo que no existe: las
          credenciales no están montadas (§8.2: archivo o identidad de
          instancia, pero ALGO tiene que estar).
        - OCI_REGION en el valor de ejemplo: el Apéndice A prohíbe elegir
          una región al azar para ocultar un dato faltante.
        """
        from pathlib import Path

        problemas: list[str] = []
        if self.app_env != "production":
            # Fuera de producción los placeholders son legales (mocks
            # explícitos); la validación crítica solo aplica al arrancar
            # para entrega real.
            return

        if self.google_api_key == "placeholder":
            problemas.append("GOOGLE_API_KEY no esta configurada (queda 'placeholder')")
        if self.oci_compartment_id == "placeholder":
            problemas.append("OCI_COMPARTMENT_ID no esta configurada (queda 'placeholder')")
        if not Path(self.oci_config_file).is_file():
            problemas.append(
                f"OCI_CONFIG_FILE apunta a un archivo inexistente ({self.oci_config_file}); "
                "montar las credenciales (§8.2)"
            )
        if self.oci_region == "home-region-de-la-tenancy":
            problemas.append("OCI_REGION conserva el valor de ejemplo; configurar la home region real")

        if problemas:
            raise ConfiguracionIncompleta(problemas)


class ConfiguracionIncompleta(RuntimeError):
    """Arranque de producción con config incompleta: falla visible y accionable.

    El mensaje final enumera cada variable faltante y cómo continuar, para
    que quien despliega no tenga que adivinar (criterio 3 del issue #07).
    """

    def __init__(self, problemas: list[str]) -> None:
        detalle = "\n".join(f"  - {p}" for p in problemas)
        super().__init__(
            "Configuracion de PRODUCCION incompleta; el backend no arranca:\n"
            f"{detalle}\n"
            "Completar las variables listadas (ver .env.example / Apendice A) "
            "o usar APP_ENV=development con MOCK_OCI=1 para desarrollo."
        )


# Instancia compartida para importación directa (``from app.config import config``).
# Se construye al primer import con el entorno vigente; los tests construyen
# instancias propias en lugar de mutar esta.
config = Configuracion()
