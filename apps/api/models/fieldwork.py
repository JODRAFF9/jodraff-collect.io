"""Terrain : echantillon, affectations, entretiens, reponses, paradonnees, synchronisation."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.models.base import (
    AssignmentStatus,
    Base,
    IdMixin,
    InterviewStatus,
    TimestampMixin,
)


class SampleUnit(Base, IdMixin, TimestampMixin):
    """Unite de l'echantillon : menage, entreprise, individu, numero de telephone."""

    __tablename__ = "sample_units"
    __table_args__ = (UniqueConstraint("survey_id", "code", name="uq_sample_unit_code"),)

    survey_id: Mapped[str] = mapped_column(ForeignKey("surveys.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
    email: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(400))

    # Decoupage geographique, du plus large au plus fin.
    geo_level_1: Mapped[str | None] = mapped_column(String(120), index=True)  # region
    geo_level_2: Mapped[str | None] = mapped_column(String(120), index=True)  # departement
    geo_level_3: Mapped[str | None] = mapped_column(String(120), index=True)  # commune
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    stratum: Mapped[str | None] = mapped_column(String(120), index=True)
    sampling_weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    attributes: Mapped[dict] = mapped_column(default=dict)

    assignments: Mapped[list["Assignment"]] = relationship(back_populates="sample_unit")


class Assignment(Base, IdMixin, TimestampMixin):
    """Affectation d'une unite d'echantillon a un enqueteur pour un mode donne."""

    __tablename__ = "assignments"
    __table_args__ = (
        Index("ix_assignment_survey_status", "survey_id", "status"),
        Index("ix_assignment_enum_status", "enumerator_id", "status"),
    )

    survey_id: Mapped[str] = mapped_column(ForeignKey("surveys.id"), nullable=False, index=True)
    sample_unit_id: Mapped[str] = mapped_column(ForeignKey("sample_units.id"), nullable=False)
    enumerator_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    collection_service: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default=AssignmentStatus.PENDING.value, nullable=False
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)  # 1 = urgent
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)  # rappel CATI
    outcome_code: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)

    sample_unit: Mapped[SampleUnit] = relationship(back_populates="assignments")
    interviews: Mapped[list["Interview"]] = relationship(back_populates="assignment")


class Interview(Base, IdMixin, TimestampMixin):
    """Un entretien : l'unite de collecte elementaire."""

    __tablename__ = "interviews"
    __table_args__ = (
        Index("ix_interview_survey_status", "survey_id", "status"),
        Index("ix_interview_enum_date", "enumerator_id", "started_at"),
        UniqueConstraint("client_uuid", name="uq_interview_client_uuid"),
    )

    survey_id: Mapped[str] = mapped_column(ForeignKey("surveys.id"), nullable=False, index=True)
    questionnaire_id: Mapped[str] = mapped_column(
        ForeignKey("questionnaires.id"), nullable=False, index=True
    )
    assignment_id: Mapped[str | None] = mapped_column(ForeignKey("assignments.id"), index=True)
    enumerator_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)

    # Identifiant genere par le client (tablette hors ligne) : garantit
    # l'idempotence de la synchronisation.
    client_uuid: Mapped[str | None] = mapped_column(String(64))

    collection_service: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(24), default=InterviewStatus.IN_PROGRESS.value, nullable=False
    )
    language: Mapped[str] = mapped_column(String(8), default="fr", nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)

    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    gps_accuracy_m: Mapped[float | None] = mapped_column(Float)
    device_id: Mapped[str | None] = mapped_column(String(64), index=True)
    app_version: Mapped[str | None] = mapped_column(String(32))

    # Controle qualite
    validated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime)
    validation_notes: Mapped[str | None] = mapped_column(Text)
    quality_score: Mapped[float | None] = mapped_column(Float)
    quality_flags: Mapped[list] = mapped_column(default=list)

    sync_batch_id: Mapped[str | None] = mapped_column(ForeignKey("sync_batches.id"), index=True)

    assignment: Mapped[Assignment | None] = relationship(back_populates="interviews")
    answers: Mapped[list["Answer"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan"
    )
    paradata: Mapped[list["Paradata"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan"
    )


class Answer(Base, IdMixin):
    """Reponse a une question, stockee en format long typé.

    Le stockage long (une ligne par question) absorbe sans migration les
    changements de questionnaire ; la mise a plat en colonnes est faite dans
    la couche silver de l'entrepot.
    """

    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint("interview_id", "question_code", "repeat_index", name="uq_answer_slot"),
        Index("ix_answer_question", "question_code"),
    )

    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id"), nullable=False, index=True
    )
    question_id: Mapped[str | None] = mapped_column(ForeignKey("questions.id"))
    # Le code est duplique : il survit a la suppression d'une version de
    # questionnaire et sert de cle d'analyse stable.
    question_code: Mapped[str] = mapped_column(String(48), nullable=False)
    question_type: Mapped[str] = mapped_column(String(24), nullable=False)
    repeat_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    value_text: Mapped[str | None] = mapped_column(Text)
    value_number: Mapped[float | None] = mapped_column(Float)
    value_date: Mapped[datetime | None] = mapped_column(DateTime)
    value_json: Mapped[dict | None] = mapped_column()  # choix multiples, geopoint, media
    is_other: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime)

    interview: Mapped[Interview] = relationship(back_populates="answers")

    @property
    def value(self):
        """Valeur unifiee, quel que soit le type."""
        if self.value_json is not None:
            return self.value_json
        if self.value_number is not None:
            return self.value_number
        if self.value_date is not None:
            return self.value_date
        return self.value_text


class Paradata(Base, IdMixin):
    """Traces d'execution de l'entretien, base du controle qualite.

    Exemples d'evenements : ouverture d'une question, retour arriere,
    modification d'une reponse deja saisie, perte de GPS, mise en pause.
    """

    __tablename__ = "paradata"
    __table_args__ = (Index("ix_paradata_interview_ts", "interview_id", "occurred_at"),)

    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    question_code: Mapped[str | None] = mapped_column(String(48))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(default=dict)

    interview: Mapped[Interview] = relationship(back_populates="paradata")


class SyncBatch(Base, IdMixin):
    """Lot de synchronisation remonte par un appareil hors ligne."""

    __tablename__ = "sync_batches"

    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    enumerator_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    survey_id: Mapped[str | None] = mapped_column(ForeignKey("surveys.id"), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    interview_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="received", nullable=False)
    app_version: Mapped[str | None] = mapped_column(String(32))
    errors: Mapped[list] = mapped_column(default=list)
