"""Schemas d'entree et de sortie de l'API."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from apps.api.services import collection_catalog as catalog


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------
# Authentification
# --------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    role: str
    full_name: str


# --------------------------------------------------------------------------
# Enquetes
# --------------------------------------------------------------------------


class SurveyCreate(BaseModel):
    code: str = Field(min_length=2, max_length=48)
    title: str = Field(min_length=3, max_length=300)
    description: str | None = None
    collection_services: list[str] = Field(min_length=1)
    target_sample: int = Field(default=0, ge=0)
    start_date: date | None = None
    end_date: date | None = None
    default_language: str = "fr"
    languages: list[str] = Field(default_factory=lambda: ["fr"])

    @field_validator("collection_services")
    @classmethod
    def _known_services(cls, value: list[str]) -> list[str]:
        normalised = []
        for code in value:
            upper = code.upper()
            if not catalog.exists(upper):
                raise ValueError(f"Service de collecte inconnu : {code}")
            normalised.append(upper)
        return normalised

    @field_validator("code")
    @classmethod
    def _slug(cls, value: str) -> str:
        cleaned = value.strip().upper().replace(" ", "_")
        if not cleaned.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Le code ne doit contenir que lettres, chiffres, tiret et underscore.")
        return cleaned


class SurveyUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    target_sample: int | None = Field(default=None, ge=0)
    start_date: date | None = None
    end_date: date | None = None


class SurveyOut(ORMModel):
    id: str
    code: str
    title: str
    description: str | None
    status: str
    collection_services: list[str]
    target_sample: int
    start_date: date | None
    end_date: date | None
    default_language: str
    languages: list[str]


# --------------------------------------------------------------------------
# Questionnaires
# --------------------------------------------------------------------------


class QuestionnaireCreate(BaseModel):
    title: str
    settings: dict[str, Any] = Field(default_factory=dict)


class QuestionnaireOut(ORMModel):
    id: str
    survey_id: str
    version: int
    title: str
    status: str
    schema_hash: str | None


class SchemaImport(BaseModel):
    """Import complet d'un questionnaire (format natif de la plateforme)."""

    title: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    choice_lists: list[dict[str, Any]] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)


class ValidationReport(BaseModel):
    is_valid: bool
    error_count: int
    warning_count: int
    issues: list[dict[str, str]]


# --------------------------------------------------------------------------
# Echantillon et affectations
# --------------------------------------------------------------------------


class SampleUnitIn(BaseModel):
    code: str
    contact_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    geo_level_1: str | None = None
    geo_level_2: str | None = None
    geo_level_3: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    stratum: str | None = None
    sampling_weight: float = 1.0
    attributes: dict[str, Any] = Field(default_factory=dict)


class SampleImport(BaseModel):
    collection_service: str
    units: list[SampleUnitIn] = Field(min_length=1)

    @field_validator("collection_service")
    @classmethod
    def _known(cls, value: str) -> str:
        upper = value.upper()
        if not catalog.exists(upper):
            raise ValueError(f"Service de collecte inconnu : {value}")
        return upper


class AutoAssignRequest(BaseModel):
    collection_service: str
    limit: int | None = Field(default=None, ge=1)
    respect_zone: bool = True
    due_in_days: int = Field(default=7, ge=1, le=90)


class ReassignRequest(BaseModel):
    new_enumerator_id: str
    reason: str = Field(min_length=3)


# --------------------------------------------------------------------------
# Enqueteurs
# --------------------------------------------------------------------------


class EnumeratorCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str = Field(min_length=8)
    phone: str | None = None
    matricule: str
    base_zone: str | None = None
    supervisor_id: str | None = None
    daily_capacity: int = Field(default=8, ge=1, le=50)
    certified_services: list[str] = Field(default_factory=list)
    training_completed: bool = False


class EnumeratorOut(BaseModel):
    user_id: str
    full_name: str
    email: str
    matricule: str
    base_zone: str | None
    daily_capacity: int
    certified_services: list[str]
    training_completed: bool
    status: str


# --------------------------------------------------------------------------
# Collecte
# --------------------------------------------------------------------------


class StartInterviewRequest(BaseModel):
    collection_service: str
    assignment_id: str | None = None
    client_uuid: str | None = None
    language: str | None = None
    device_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class AnswerIn(BaseModel):
    question_code: str
    value: Any = None
    repeat_index: int = 0
    duration_ms: int | None = None


class AnswerBatch(BaseModel):
    answers: list[AnswerIn] = Field(min_length=1)


class InterviewOut(ORMModel):
    id: str
    survey_id: str
    questionnaire_id: str
    collection_service: str
    status: str
    language: str
    duration_seconds: int | None
    quality_score: float | None
    quality_flags: list[str]


class CompleteRequest(BaseModel):
    allow_partial: bool = False


class ValidateInterviewRequest(BaseModel):
    accepted: bool
    notes: str | None = None


class SyncPayload(BaseModel):
    device_id: str
    app_version: str | None = None
    interviews: list[dict[str, Any]] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Quotas
# --------------------------------------------------------------------------


class QuotaIn(BaseModel):
    label: str
    dimensions: dict[str, Any]
    target: int = Field(ge=1)
    is_blocking: bool = True


# --------------------------------------------------------------------------
# Estimation a l'entree de la plateforme
# --------------------------------------------------------------------------


class EstimateRequest(BaseModel):
    collection_services: list[str] = Field(min_length=1)
    sample_size: int = Field(ge=1)

    @field_validator("collection_services")
    @classmethod
    def _known_services(cls, value: list[str]) -> list[str]:
        for code in value:
            if not catalog.exists(code):
                raise ValueError(f"Service de collecte inconnu : {code}")
        return [c.upper() for c in value]
