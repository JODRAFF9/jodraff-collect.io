"""Socle ORM : classe de base, types portables et enumerations metier."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    """Base declarative commune, avec JSON portable SQLite / Postgres / MSSQL."""

    type_annotation_map = {dict: JSON, list: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class IdMixin:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)


# --------------------------------------------------------------------------
# Enumerations metier
# --------------------------------------------------------------------------


class UserRole(str, Enum):
    ADMIN = "admin"  # administration de la plateforme
    METHODOLOGIST = "methodologist"  # conception des questionnaires
    SUPERVISOR = "supervisor"  # supervision du terrain
    ENUMERATOR = "enumerator"  # enqueteur
    ANALYST = "analyst"  # traitement et restitution
    CLIENT = "client"  # commanditaire, lecture seule


class SurveyStatus(str, Enum):
    DRAFT = "draft"
    DESIGN = "design"
    PILOT = "pilot"
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"
    ARCHIVED = "archived"


class QuestionnaireStatus(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class QuestionType(str, Enum):
    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    DATE = "date"
    TIME = "time"
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    SCALE = "scale"
    NOTE = "note"
    CALCULATE = "calculate"
    GEOPOINT = "geopoint"
    PHOTO = "photo"
    AUDIO = "audio"
    FILE = "file"
    BARCODE = "barcode"
    SIGNATURE = "signature"

    @property
    def is_numeric(self) -> bool:
        return self in {QuestionType.INTEGER, QuestionType.DECIMAL, QuestionType.SCALE}

    @property
    def is_choice(self) -> bool:
        return self in {QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE}


class AssignmentStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    UNREACHABLE = "unreachable"
    REFUSED = "refused"
    REASSIGNED = "reassigned"


class InterviewStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"  # termine sur le terrain
    SUBMITTED = "submitted"  # remonte au serveur
    VALIDATED = "validated"  # controle qualite passe
    REJECTED = "rejected"  # a refaire
    PARTIAL = "partial"  # abandonne en cours
    REFUSED = "refused"


TERMINAL_INTERVIEW_STATUSES = {
    InterviewStatus.VALIDATED,
    InterviewStatus.REJECTED,
    InterviewStatus.REFUSED,
}
