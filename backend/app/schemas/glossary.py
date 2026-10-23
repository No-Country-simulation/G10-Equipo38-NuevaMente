"""Glosario #39: candidatos internos y proyección pública del material revisado."""

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.enums import OutputLanguage, RecipientProfile
from app.schemas.internal import TrabajoComunResponse
from app.schemas.pedagogical import ModeloContenido, Referencia
from app.schemas.responses import DocumentoFuente, EvaluacionCalidad, Trazabilidad


class TerminoCandidato(ModeloContenido):
    id: str = Field(min_length=1, max_length=64)
    termino_original: str = Field(min_length=1, max_length=100)
    es_identificador: bool
    ubicaciones: list[Referencia] = Field(min_length=1, max_length=8)


class BorradorTermino(ModeloContenido):
    id: str = Field(min_length=1, max_length=64)
    traduccion: str | None = Field(default=None, min_length=1, max_length=100)
    estado: Literal["definido", "sin_definicion_suficiente"]
    definicion: str | None = Field(default=None, min_length=1, max_length=2000)
    referencias: list[Referencia] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def coherente(self):
        if self.estado == "definido" and (not self.definicion or not self.referencias):
            raise ValueError("Una definición requiere contenido y citas")
        if self.estado == "sin_definicion_suficiente" and (self.definicion or self.traduccion or self.referencias):
            raise ValueError("Una limitación no introduce definiciones, traducciones ni citas del modelo")
        return self


class BorradorGlosario(ModeloContenido):
    terminos: list[BorradorTermino] = Field(min_length=1, max_length=64)


class TerminoGlosario(BorradorTermino):
    termino_original: str = Field(min_length=1, max_length=100)
    es_identificador: bool
    limitacion: str | None = None

    @model_validator(mode="after")
    def coherente(self):
        # Las citas de una limitación las fija el backend: prueban la aparición,
        # no una definición. No son las referencias propuestas por un LLM.
        if not self.referencias:
            raise ValueError("Cada término requiere su ubicación de origen")
        if self.estado == "definido" and (not self.definicion or self.limitacion):
            raise ValueError("Definición incompleta o marcada como limitación")
        if self.estado == "sin_definicion_suficiente" and (self.definicion or self.traduccion or not self.limitacion):
            raise ValueError("La limitación debe ser explícita y sin contenido inventado")
        if self.es_identificador and self.traduccion is not None:
            raise ValueError("Productos, comandos y APIs conservan su nombre original")
        return self


class GlossaryOutput(ModeloContenido):
    schema_version: Literal["1.0"] = "1.0"
    glossary_id: str
    documento_fuente: DocumentoFuente
    documentos_fuente: list[DocumentoFuente] = Field(
        default_factory=list,
        description="Fuentes autorizadas del glosario (§6.2).",
    )
    perfil_destinatario: RecipientProfile
    idioma_salida: OutputLanguage
    estado: Literal["verificado", "sin_definiciones", "sin_terminos"]
    terminos: list[TerminoGlosario] = Field(max_length=64)
    candidatos_omitidos: int = Field(ge=0)
    evaluacion_calidad: EvaluacionCalidad | None
    trazabilidad: Trazabilidad | None
    prompt_version: str
    created_at: AwareDatetime

    @model_validator(mode="after")
    def aprobado(self):
        if not self.documentos_fuente and self.documento_fuente:
            self.documentos_fuente = [self.documento_fuente]
        elif self.documentos_fuente and not self.documento_fuente:
            self.documento_fuente = self.documentos_fuente[0]
        elif self.documento_fuente and self.documentos_fuente:
            doc_ids = [d.document_id for d in self.documentos_fuente]
            if self.documento_fuente.document_id not in doc_ids:
                if len(self.documentos_fuente) <= 1:
                    self.documentos_fuente = [self.documento_fuente]
                else:
                    self.documentos_fuente.insert(0, self.documento_fuente)
            else:
                idx = doc_ids.index(self.documento_fuente.document_id)
                self.documentos_fuente[idx] = self.documento_fuente
        ids = [t.id for t in self.terminos]
        definidos = any(t.estado == "definido" for t in self.terminos)
        if len(ids) != len(set(ids)):
            raise ValueError("Términos duplicados")
        if self.estado == "verificado" and (
            not definidos
            or self.evaluacion_calidad is None
            or self.evaluacion_calidad.estado_evaluacion != "aprobada"
            or self.trazabilidad is None
        ):
            raise ValueError("Las definiciones requieren revisión completa aprobada")
        if self.estado != "verificado" and (definidos or self.evaluacion_calidad is not None):
            raise ValueError("La ausencia de definiciones no obtiene un score de aprobación")
        if (self.estado == "sin_terminos") != (not self.terminos):
            raise ValueError("El estado debe corresponder al inventario de términos")
        return self


class GlossaryJobResponse(TrabajoComunResponse):
    glossary_id: str
    result: GlossaryOutput | None = None
