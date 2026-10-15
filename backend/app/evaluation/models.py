"""Contratos offline para material, anotaciones y correcciones del #60."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.core.agents.graph_state import BorradorPedagogico
from app.schemas.enums import DetailLevel, IndustryNiche, OutputLanguage, PedagogicalFormat, RecipientProfile
from app.schemas.pedagogical import Referencia
from app.schemas.responses import DocumentoFuente, EvaluacionCalidad, PedagogicalOutput, Trazabilidad

Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,79}$")]
Score = Annotated[int, Field(strict=True, ge=1, le=4)]
Decision = Literal["aprobado", "rechazado", "no_evaluable"]
DIMENSIONS = ("claridad", "adecuacion_perfil", "cobertura", "utilidad")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class Source(Contract):
    path: Literal[
        "documents/redes_vcn_oci.pdf", "documents/integracion_apis_pagos.md", "documents/gobernanza_datos_salud.md"
    ]
    sha256: Sha
    previous_hashes: list[Sha] = Field(default_factory=list)


class Parameters(Contract):
    perfil_destinatario: RecipientProfile
    formato_salida: PedagogicalFormat
    nicho_sector: IndustryNiche
    nivel_detalle: DetailLevel
    idioma_salida: OutputLanguage


class Case(Contract):
    case_id: Identifier
    source: Literal["vcn", "pagos", "salud"]
    parameters: Parameters
    objectives: list[str] = Field(min_length=2)


class Dataset(Contract):
    dataset_id: Identifier
    version: str = Field(min_length=1)
    sources: dict[str, Source]
    cases: list[Case] = Field(min_length=12)

    @model_validator(mode="after")
    def coverage(self):
        if set(self.sources) != {"vcn", "pagos", "salud"}:
            raise ValueError("El conjunto debe identificar los tres documentos demo")
        expected = {
            "vcn": "documents/redes_vcn_oci.pdf",
            "pagos": "documents/integracion_apis_pagos.md",
            "salud": "documents/gobernanza_datos_salud.md",
        }
        if {key: source.path for key, source in self.sources.items()} != expected:
            raise ValueError("Cada identificador de fuente debe apuntar a su documento demo")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("IDs de caso duplicados")
        if {case.source for case in self.cases} != set(self.sources):
            raise ValueError("Falta cobertura de documentos demo")
        for language in OutputLanguage:
            cases = [case for case in self.cases if case.parameters.idioma_salida == language]
            if {case.parameters.perfil_destinatario for case in cases} != set(RecipientProfile):
                raise ValueError(f"Faltan perfiles en {language.value}")
            if len({case.parameters.formato_salida for case in cases}) < 3:
                raise ValueError(f"Faltan formatos en {language.value}")
        return self


class EvaluationArtifact(Contract):
    dataset_id: Identifier
    case_id: Identifier
    revision: int = Field(default=1, strict=True, ge=1)
    previous_digest: Sha | None = None
    mode: Literal["real", "mock"]
    captured_at: AwareDatetime
    source: DocumentoFuente
    draft: BorradorPedagogico
    judge: EvaluacionCalidad
    references: list[Referencia] = Field(min_length=1)
    trace: Trazabilidad

    @model_validator(mode="after")
    def revision_chain(self):
        if (self.revision == 1) != (self.previous_digest is None):
            raise ValueError("Cada revisión posterior debe enlazar el artefacto anterior")
        if self.trace.parser_version.strip() == "" or self.trace.prompt_version.strip() == "":
            raise ValueError("La procedencia requiere versiones de parser y prompt")
        return self

    def digest(self):
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @classmethod
    def from_package(cls, dataset_id, case_id, package: PedagogicalOutput, mode):
        return cls(
            dataset_id=dataset_id,
            case_id=case_id,
            mode=mode,
            captured_at=package.created_at,
            source=package.documento_fuente,
            draft=BorradorPedagogico(contenido_adaptado=package.contenido_adaptado, metadatos=package.metadatos),
            judge=package.evaluacion_calidad,
            references=package.referencias,
            trace=package.trazabilidad,
        )


class Annotation(Contract):
    case_id: Identifier
    artifact_digest: Sha
    claridad: Score | None = None
    adecuacion_perfil: Score | None = None
    cobertura: Score | None = None
    utilidad: Score | None = None
    technical_errors: StrictBool | None = None
    citations_valid: StrictBool | None = None
    language_valid: StrictBool | None = None
    decision: Decision | None = None
    rationale: str = ""

    @property
    def complete(self):
        return (
            all(getattr(self, dimension) is not None for dimension in DIMENSIONS)
            and all(
                getattr(self, flag) is not None for flag in ("technical_errors", "citations_valid", "language_valid")
            )
            and self.decision is not None
            and len(self.rationale.strip()) >= 10
        )

    @model_validator(mode="after")
    def approval(self):
        if self.decision == "aprobado" and (
            not self.complete
            or any(getattr(self, dimension) < 3 for dimension in DIMENSIONS)
            or self.technical_errors
            or not self.citations_valid
            or not self.language_valid
        ):
            raise ValueError("La aprobación humana debe ser coherente con las notas y comprobaciones")
        return self


class Review(Contract):
    dataset_id: Identifier
    reviewer_id: Identifier
    human_confirmed: StrictBool = False
    independent: StrictBool = False
    submitted_at: AwareDatetime | None = None
    annotations: list[Annotation] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_annotations(self):
        keys = [(a.case_id, a.artifact_digest) for a in self.annotations]
        if len(keys) != len(set(keys)):
            raise ValueError("Anotaciones duplicadas para el mismo artefacto")
        if self.human_confirmed and (not self.independent or self.submitted_at is None):
            raise ValueError("La revisión humana requiere independencia declarada y fecha")
        if self.human_confirmed and self.reviewer_id in ("revisor-a", "revisor-b"):
            raise ValueError("Reemplazar el identificador de plantilla por el del revisor humano")
        return self


class Adjudication(Contract):
    artifact_digest: Sha
    decision: Literal["aprobado", "rechazado"]
    rationale: str = Field(min_length=10)
    decided_by: Identifier
    decided_at: AwareDatetime
    human_confirmed: StrictBool

    @model_validator(mode="after")
    def human_decision(self):
        if not self.human_confirmed:
            raise ValueError("La adjudicación requiere confirmación humana")
        return self


class Correction(Contract):
    case_id: Identifier
    original_digest: Sha
    updated_digest: Sha
    change: Literal["prompt", "retrieval", "judge", "source"]
    description: str = Field(min_length=10)
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")


class ResolutionLog(Contract):
    adjudications: list[Adjudication] = Field(default_factory=list)
    corrections: list[Correction] = Field(default_factory=list)
