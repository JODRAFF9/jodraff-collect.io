"""Conception : enquetes, versions de questionnaire, sections, questions, modalites."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.models.base import (
    Base,
    IdMixin,
    QuestionnaireStatus,
    SurveyStatus,
    TimestampMixin,
)


class Survey(Base, IdMixin, TimestampMixin):
    """Une enquete : le projet dans son ensemble, tous modes confondus."""

    __tablename__ = "surveys"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_survey_org_code"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default=SurveyStatus.DRAFT.value, nullable=False)

    # Choix effectue a l'entree de la plateforme, ex. ["CAWI", "CATI"].
    collection_services: Mapped[list] = mapped_column(default=list, nullable=False)

    target_sample: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    default_language: Mapped[str] = mapped_column(String(8), default="fr", nullable=False)
    languages: Mapped[list] = mapped_column(default=lambda: ["fr"], nullable=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))

    questionnaires: Mapped[list["Questionnaire"]] = relationship(
        back_populates="survey", cascade="all, delete-orphan", order_by="Questionnaire.version"
    )
    quotas: Mapped[list["Quota"]] = relationship(
        back_populates="survey", cascade="all, delete-orphan"
    )

    @property
    def published_questionnaire(self) -> "Questionnaire | None":
        published = [
            q for q in self.questionnaires if q.status == QuestionnaireStatus.PUBLISHED.value
        ]
        return max(published, key=lambda q: q.version) if published else None


class Questionnaire(Base, IdMixin, TimestampMixin):
    """Version figee d'un questionnaire.

    Une version publiee n'est jamais modifiee : toute evolution cree une
    nouvelle version, ce qui garantit que chaque entretien reste rattachable
    a l'instrument exact qui l'a produit.
    """

    __tablename__ = "questionnaires"
    __table_args__ = (UniqueConstraint("survey_id", "version", name="uq_questionnaire_version"),)

    survey_id: Mapped[str] = mapped_column(ForeignKey("surveys.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default=QuestionnaireStatus.DRAFT.value, nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    published_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    schema_hash: Mapped[str | None] = mapped_column(String(64))
    settings: Mapped[dict] = mapped_column(default=dict)

    survey: Mapped[Survey] = relationship(back_populates="questionnaires")
    sections: Mapped[list["Section"]] = relationship(
        back_populates="questionnaire",
        cascade="all, delete-orphan",
        order_by="Section.order_index",
    )
    choice_lists: Mapped[list["ChoiceList"]] = relationship(
        back_populates="questionnaire", cascade="all, delete-orphan"
    )

    @property
    def is_editable(self) -> bool:
        return self.status in {QuestionnaireStatus.DRAFT.value, QuestionnaireStatus.REVIEW.value}

    def iter_questions(self):
        for section in sorted(self.sections, key=lambda s: s.order_index):
            yield from sorted(section.questions, key=lambda q: q.order_index)


class Section(Base, IdMixin, TimestampMixin):
    """Bloc de questions, potentiellement repetable (roster menage par exemple)."""

    __tablename__ = "sections"
    __table_args__ = (UniqueConstraint("questionnaire_id", "code", name="uq_section_code"),)

    questionnaire_id: Mapped[str] = mapped_column(
        ForeignKey("questionnaires.id"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    label: Mapped[dict] = mapped_column(default=dict, nullable=False)  # {"fr": "...", "en": "..."}
    description: Mapped[dict] = mapped_column(default=dict)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_repeatable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    repeat_count_question: Mapped[str | None] = mapped_column(String(48))
    relevance: Mapped[str | None] = mapped_column(String(500))  # condition d'affichage

    questionnaire: Mapped[Questionnaire] = relationship(back_populates="sections")
    questions: Mapped[list["Question"]] = relationship(
        back_populates="section", cascade="all, delete-orphan", order_by="Question.order_index"
    )


class ChoiceList(Base, IdMixin, TimestampMixin):
    """Liste de modalites reutilisable entre plusieurs questions."""

    __tablename__ = "choice_lists"
    __table_args__ = (UniqueConstraint("questionnaire_id", "code", name="uq_choicelist_code"),)

    questionnaire_id: Mapped[str] = mapped_column(
        ForeignKey("questionnaires.id"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    questionnaire: Mapped[Questionnaire] = relationship(back_populates="choice_lists")
    choices: Mapped[list["Choice"]] = relationship(
        back_populates="choice_list", cascade="all, delete-orphan", order_by="Choice.order_index"
    )


class Choice(Base, IdMixin):
    __tablename__ = "choices"
    __table_args__ = (UniqueConstraint("choice_list_id", "code", name="uq_choice_code"),)

    choice_list_id: Mapped[str] = mapped_column(
        ForeignKey("choice_lists.id"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    label: Mapped[dict] = mapped_column(default=dict, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)  # valeur numerique pour les scores
    is_exclusive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    choice_list: Mapped[ChoiceList] = relationship(back_populates="choices")


class Question(Base, IdMixin, TimestampMixin):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("section_id", "code", name="uq_question_code"),)

    section_id: Mapped[str] = mapped_column(ForeignKey("sections.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[dict] = mapped_column(default=dict, nullable=False)
    hint: Mapped[dict] = mapped_column(default=dict)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Logique du questionnaire
    relevance: Mapped[str | None] = mapped_column(String(500))  # ex. "Q1 == 'oui'"
    constraint_expr: Mapped[str | None] = mapped_column(String(500))  # ex. "value >= 0"
    constraint_message: Mapped[dict] = mapped_column(default=dict)
    calculation: Mapped[str | None] = mapped_column(String(500))  # type calculate

    min_value: Mapped[float | None] = mapped_column(Float)
    max_value: Mapped[float | None] = mapped_column(Float)
    choice_list_id: Mapped[str | None] = mapped_column(ForeignKey("choice_lists.id"))
    allow_other: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Restriction eventuelle a certains modes, ex. ["CAPI"] pour une photo.
    only_services: Mapped[list] = mapped_column(default=list)
    # Marque les variables sensibles (donnees personnelles) pour l'anonymisation.
    is_pii: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    analysis_role: Mapped[str | None] = mapped_column(String(32))  # dimension | mesure | identifiant

    section: Mapped[Section] = relationship(back_populates="questions")
    choice_list: Mapped[ChoiceList | None] = relationship()


class Quota(Base, IdMixin, TimestampMixin):
    """Quota de collecte sur un croisement de variables.

    ``dimensions`` porte le croisement, par exemple {"region": "Dakar",
    "sexe": "F"} ; ``achieved`` est incremente a chaque entretien valide.
    """

    __tablename__ = "quotas"

    survey_id: Mapped[str] = mapped_column(ForeignKey("surveys.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    dimensions: Mapped[dict] = mapped_column(default=dict, nullable=False)
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    achieved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_blocking: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    survey: Mapped[Survey] = relationship(back_populates="quotas")

    @property
    def completion_rate(self) -> float:
        return round(self.achieved / self.target, 4) if self.target else 0.0

    @property
    def is_full(self) -> bool:
        return self.achieved >= self.target
