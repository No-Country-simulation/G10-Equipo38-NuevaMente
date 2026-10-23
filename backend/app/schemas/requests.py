"""Peticiones del contrato v1 (issue #03, referencia §16.1 y §7.1).

Modelos de ENTRADA: lo que el frontend (u otro consumidor autorizado) envía
al backend. Cada uno corresponde a un endpoint de docs/contratos-api.md.

Dos cosas que se repiten en todos:

- ``extra="forbid"``: campos desconocidos se rechazan (§7.2). Enviar
  ``formato_salida`` mal escrito falla ruidosamente, en vez de aceptarse y
  generar con el valor por defecto equivocado.
- Los enums vienen de enums.py: los valores ya están validados por la lista
  cerrada; aquí solo se combinan.

Nota sobre alcance (§16.1): el documento completo es el default conceptual,
y omitir alcance selecciona documento_completo; una sección exige su ID.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.enums import DetailLevel, IndustryNiche, OutputLanguage, PedagogicalFormat, RecipientProfile
from app.schemas.pedagogical import AlcanceSolicitud, ModeloContenido


class GenerateRequest(ModeloContenido):
    """Body de POST /api/generate (§16.1: document_id + parámetros de adaptación).

      Devuelve 202 con generation_id y URLs (no el contenido): la generación
    es asíncrona y se sigue por status_url/events_url (§3.3).
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str | None = Field(default=None, description="Documento ready del espacio (§7.2).")
    document_ids: list[str] = Field(
        default_factory=list, description="Lista de documentos para multidocumental (§5.1)."
    )
    perfil_destinatario: RecipientProfile
    formato_salida: PedagogicalFormat
    nicho_sector: IndustryNiche
    nivel_detalle: DetailLevel
    idioma_salida: OutputLanguage
    modo_generacion: Literal["multiagente", "basico"] = "multiagente"
    alcance: AlcanceSolicitud = Field(
        default_factory=AlcanceSolicitud, description="Documento completo, sección explícita o multi_documento."
    )

    @model_validator(mode="after")
    def _normalizar_fuentes(self) -> "GenerateRequest":
        ids_limpios = []
        if self.document_ids:
            for item in self.document_ids:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError("Cada document_id en document_ids debe ser una cadena no vacía")
                ids_limpios.append(item.strip())
            self.document_ids = sorted(list(set(ids_limpios)))

        if len(self.document_ids) > 5:
            raise ValueError("No se permiten más de 5 documentos por solicitud")

        if self.document_id is not None:
            doc_id_limpio = self.document_id.strip()
            if not doc_id_limpio:
                raise ValueError("document_id no puede estar vacío")
            self.document_id = doc_id_limpio
            if self.document_ids:
                primario = self.document_ids[0]
                if self.document_id != primario:
                    raise ValueError(
                        f"document_id '{self.document_id}' contradictorio con el primario canónico '{primario}'"
                    )
            else:
                self.document_ids = [self.document_id]
        elif self.document_ids:
            self.document_id = self.document_ids[0]
        elif self.alcance.tipo != "espacio_completo":
            raise ValueError("Debe especificarse document_id o document_ids")

        if len(self.document_ids) > 1:
            if self.alcance.tipo == "seccion":
                raise ValueError("alcance.tipo='seccion' no admite múltiples fuentes")
            if self.alcance.tipo == "documento_completo":
                self.alcance.tipo = "multi_documento"
            self.alcance.document_ids = list(self.document_ids)
        elif len(self.document_ids) == 1 and self.alcance.tipo == "multi_documento":
            raise ValueError("alcance multi_documento requiere al menos 2 fuentes")

        return self

    def parametros_persistidos(self):
        datos = self.model_dump(mode="json")
        # Mantener la identidad idempotente de solicitudes anteriores al selector.
        if self.modo_generacion == "multiagente":
            datos.pop("modo_generacion")
        # Conservar hash_cuerpo para peticiones históricas unidocumentales sin document_ids explícito:
        if "document_ids" not in self.model_fields_set and len(self.document_ids) <= 1:
            datos.pop("document_ids", None)
            if "alcance" in datos and "document_ids" in datos["alcance"]:
                datos["alcance"].pop("document_ids", None)
        return datos


class UploadRequest(ModeloContenido):
    """Body JSON de POST /api/documents/upload para TEXTO PLANO (§16.1).

    "La carga de texto plano admite documento_titulo y documento_contenido;
    se transforma en un documento TXT con ID". La variante con archivo
    (multipart/form-data) no es JSON y se tipa en la capa de API (#19).

    Límites de la tabla de contratos: 5 MB para MD/TXT equivalente y 100k
    tokens; el conteo fino de tokens lo hará el parser (issue #11), aquí se
    fija el mínimo estructural.
    """

    model_config = ConfigDict(extra="forbid")

    documento_titulo: str = Field(min_length=1, max_length=300)
    documento_contenido: str = Field(min_length=1, description="No vacío; el parser aplica límites (#11).")


class ChatRequest(ModeloContenido):
    """Body de POST /api/chat (contratos-api.md, issues #37/#38).

    El chat es RAG sobre el documento activo: pregunta + mismo perfil e
    idioma de la conversación educativa. Devuelve 202 con URLs del trabajo;
    su result trae la respuesta revisada con citas o una abstención explícita.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    pregunta: str = Field(min_length=1, max_length=2000)
    perfil_destinatario: RecipientProfile
    idioma_salida: OutputLanguage


class GlossaryRequest(ModeloContenido):
    """Body de POST /api/glossaries (issues #39/#40 y §6.2).

    202 con glossary_id y URLs del trabajo, o 200 directo si hay caché
    aprobada para esa combinación documento+perfil+idioma (la clave de caché
    incluye esos cuatro ejes, §17.3, y todas las fuentes en multidocumental).
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str | None = Field(default=None, max_length=128)
    document_ids: list[str] = Field(
        default_factory=list, description="Lista de documentos para glosario multidocumental (§6.2)."
    )
    perfil_destinatario: RecipientProfile
    idioma_salida: OutputLanguage

    @model_validator(mode="after")
    def _normalizar_fuentes(self) -> "GlossaryRequest":
        ids_limpios = []
        if self.document_ids:
            for item in self.document_ids:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError("Cada document_id en document_ids debe ser una cadena no vacía")
                ids_limpios.append(item.strip())
            self.document_ids = sorted(list(set(ids_limpios)))

        if len(self.document_ids) > 5:
            raise ValueError("No se permiten más de 5 documentos por solicitud")

        if self.document_id is not None:
            doc_id_limpio = self.document_id.strip()
            if not doc_id_limpio:
                raise ValueError("document_id no puede estar vacío")
            self.document_id = doc_id_limpio
            if self.document_ids:
                primario = self.document_ids[0]
                if self.document_id != primario:
                    raise ValueError(
                        f"document_id '{self.document_id}' contradictorio con el primario canónico '{primario}'"
                    )
            else:
                self.document_ids = [self.document_id]
        elif self.document_ids:
            self.document_id = self.document_ids[0]
        else:
            raise ValueError("Debe especificarse document_id o document_ids")

        return self


class QuizAnswerRequest(ModeloContenido):
    """Body de POST /api/quizzes/{generation_id}/answers (issue #35).

    La corrección es determinista (comparar contra el aprobado) y NO gasta
    otra llamada LLM (§7.5). ``event_id`` alimenta el evento interno
    respuesta_quiz del progreso con idempotencia.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=64)
    option_id: str = Field(min_length=1, max_length=64)
    event_id: str = Field(min_length=1, max_length=128, description="Identificador de evento idempotente.")


class ProgressEvent(ModeloContenido):
    """Body de POST /api/progress/events (issue #35; consumidor UI #41).

    Idempotencia por ``event_id`` estable: reintentar el mismo evento (p. ej.
    tras una reconexión) NO duplica el progreso (criterio 11 de §12.2).

    Qué significa cada campo:

    - ``tipo``: solo los eventos PÚBLICOS que puede disparar la UI. El
      evento respuesta_quiz lo crea internamente el endpoint de respuestas
      del quiz, y métricas como primer_intento o acierto las calcula
      EXCLUSIVAMENTE el backend (contratos-api.md): el cliente no puede
      auto-reportarse un acierto.
    - los identificadores de referencia son opcionales porque cada tipo de
      evento relaciona recursos distintos (concepto, flashcard, documento).
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(
        min_length=1, max_length=128, description="Clave de idempotencia estable por intención de evento."
    )
    tipo: Literal["concepto_revisado", "flashcard_vista"]
    document_id: str | None = Field(default=None, min_length=1, max_length=128)
    generation_id: str | None = Field(default=None, min_length=1, max_length=128)
    flashcard_id: str | None = Field(default=None, min_length=1, max_length=64)
    concepto: str | None = Field(default=None, min_length=1, max_length=500, description="Concepto revisado.")

    @model_validator(mode="after")
    def _tipo_concepto_exige_concepto(self) -> "ProgressEvent":
        """Un evento concepto_revisado sin concepto no aporta nada al progreso."""
        if self.tipo == "concepto_revisado" and not self.concepto:
            raise ValueError("tipo='concepto_revisado' exige concepto no vacío")
        if self.tipo == "concepto_revisado" and not self.document_id:
            raise ValueError("concepto_revisado exige document_id")
        if self.tipo == "flashcard_vista" and (not self.generation_id or not self.flashcard_id):
            raise ValueError("flashcard_vista exige generation_id y flashcard_id")
        if self.tipo == "concepto_revisado" and self.flashcard_id is not None:
            raise ValueError("concepto_revisado no admite flashcard_id")
        if self.tipo == "flashcard_vista" and self.concepto is not None:
            raise ValueError("flashcard_vista no admite concepto")
        return self


class RecoverSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recovery_code: str = Field(description="Código de recuperación del espacio")
