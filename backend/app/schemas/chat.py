"""Precisión de transporte del tutor #37, sin ampliar formatos pedagógicos v1."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.enums import OutputLanguage, RecipientProfile
from app.schemas.pedagogical import Referencia
from app.schemas.responses import DocumentoFuente, EvaluacionCalidad


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str | None = Field(default=None, max_length=128)
    document_ids: list[str] = Field(
        default_factory=list, description="Lista de documentos para multidocumental (§6.1)."
    )
    pregunta: str = Field(min_length=1, max_length=2000)
    perfil_destinatario: RecipientProfile
    idioma_salida: OutputLanguage

    @field_validator("pregunta")
    @classmethod
    def pregunta_no_vacia(cls, valor):
        if not valor.strip():
            raise ValueError("La pregunta no puede estar vacía")
        return valor.strip()

    @model_validator(mode="after")
    def _normalizar_fuentes(self) -> "ChatRequest":
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


class BorradorChat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fuera_de_alcance: bool
    respuesta: str = Field(max_length=5000)
    referencias: list[Referencia] = Field(max_length=100)

    @model_validator(mode="after")
    def respuesta_coherente(self):
        if self.fuera_de_alcance:
            if self.respuesta or self.referencias:
                raise ValueError("Una abstención no puede introducir contenido")
        elif not self.respuesta.strip() or not self.referencias:
            raise ValueError("Una respuesta requiere texto y citas")
        return self


class ChatAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    document_hash: str
    document_ids: list[str] = Field(default_factory=list)
    documentos_fuente: list[DocumentoFuente] = Field(default_factory=list)
    pregunta: str
    perfil_destinatario: RecipientProfile
    idioma_salida: OutputLanguage
    estado: Literal["respondida", "fuera_de_alcance"]
    respuesta: str
    referencias: list[Referencia]
    evaluacion_calidad: EvaluacionCalidad | None

    @model_validator(mode="after")
    def aprobacion(self):
        if not self.document_ids and self.document_id:
            self.document_ids = [self.document_id]
        if self.estado == "respondida" and (
            not self.referencias
            or self.evaluacion_calidad is None
            or self.evaluacion_calidad.estado_evaluacion != "aprobada"
        ):
            raise ValueError("La respuesta requiere citas y revisión aprobada")
        if self.estado == "fuera_de_alcance" and (self.referencias or self.evaluacion_calidad is not None):
            raise ValueError("La abstención no es material aprobado")
        return self
