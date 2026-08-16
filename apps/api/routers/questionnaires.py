"""Conception des questionnaires : edition, validation, publication, versions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.db import get_session
from apps.api.deps import get_survey, require_roles
from apps.api.models import Questionnaire, Survey, User, UserRole
from apps.api.schemas import (
    QuestionnaireCreate,
    QuestionnaireOut,
    SchemaImport,
    ValidationReport,
)
from apps.api.services import collection_catalog as catalog
from apps.api.services import questionnaire as qs

router = APIRouter(prefix="/api/v1/surveys/{survey_id}/questionnaires", tags=["Questionnaires"])

_DESIGNERS = require_roles(UserRole.METHODOLOGIST, UserRole.SUPERVISOR)


def _load(session: Session, survey: Survey, questionnaire_id: str) -> Questionnaire:
    questionnaire = session.get(Questionnaire, questionnaire_id)
    if questionnaire is None or questionnaire.survey_id != survey.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Questionnaire introuvable.")
    return questionnaire


@router.get("", response_model=list[QuestionnaireOut])
def list_versions(
    survey: Survey = Depends(get_survey), session: Session = Depends(get_session)
) -> list[Questionnaire]:
    return list(
        session.scalars(
            select(Questionnaire)
            .where(Questionnaire.survey_id == survey.id)
            .order_by(Questionnaire.version.desc())
        ).all()
    )


@router.post("", response_model=QuestionnaireOut, status_code=status.HTTP_201_CREATED)
def create_version(
    payload: QuestionnaireCreate,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(_DESIGNERS),
) -> Questionnaire:
    next_version = max((q.version for q in survey.questionnaires), default=0) + 1
    questionnaire = Questionnaire(
        survey_id=survey.id,
        version=next_version,
        title=payload.title,
        settings=payload.settings,
    )
    session.add(questionnaire)
    session.flush()
    return questionnaire


@router.get("/{questionnaire_id}", summary="Schema complet du questionnaire")
def get_schema(
    questionnaire_id: str,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
) -> dict:
    return qs.to_schema(_load(session, survey, questionnaire_id))


@router.put("/{questionnaire_id}", summary="Remplacer le contenu d'un brouillon")
def import_schema(
    questionnaire_id: str,
    payload: SchemaImport,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(_DESIGNERS),
) -> dict:
    questionnaire = _load(session, survey, questionnaire_id)
    try:
        qs.from_schema(session, questionnaire, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return qs.to_schema(questionnaire)


@router.post("/{questionnaire_id}/validate", response_model=ValidationReport)
def validate_schema(
    questionnaire_id: str,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
) -> ValidationReport:
    """Controle de coherence, y compris la compatibilite avec les modes retenus."""
    questionnaire = _load(session, survey, questionnaire_id)
    issues = qs.validate(questionnaire, survey)
    errors = [i for i in issues if i.severity == "error"]
    return ValidationReport(
        is_valid=not errors,
        error_count=len(errors),
        warning_count=len(issues) - len(errors),
        issues=[i.to_dict() for i in issues],
    )


@router.post("/{questionnaire_id}/publish", summary="Publier une version")
def publish_version(
    questionnaire_id: str,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(_DESIGNERS),
) -> dict:
    questionnaire = _load(session, survey, questionnaire_id)
    try:
        return qs.publish(session, questionnaire, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{questionnaire_id}/new-version", response_model=QuestionnaireOut)
def duplicate_version(
    questionnaire_id: str,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(_DESIGNERS),
) -> Questionnaire:
    """Cree un brouillon a partir d'une version existante (publiee ou non)."""
    source = _load(session, survey, questionnaire_id)
    return qs.new_version(session, source)


@router.get("/{questionnaire_id}/compatibility", summary="Compatibilite avec les modes de collecte")
def compatibility(
    questionnaire_id: str,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
) -> dict:
    """Detaille, mode par mode, les questions non supportees.

    Utile en conception multimode : le questionnaire doit tenir dans le mode
    le plus contraint, sauf a restreindre explicitement certaines questions.
    """
    questionnaire = _load(session, survey, questionnaire_id)
    questions = list(questionnaire.iter_questions())
    report = {}
    for code in survey.collection_services:
        service = catalog.get_service(code)
        allowed = set(service.allowed_question_types)
        blocking = [
            q.code
            for q in questions
            if q.type not in allowed and (not q.only_services or code in q.only_services)
        ]
        restricted = [q.code for q in questions if q.only_services and code not in q.only_services]
        report[code] = {
            "service_name": service.name,
            "question_count": len(questions) - len(restricted),
            "unsupported_questions": blocking,
            "excluded_by_design": restricted,
            "is_compatible": not blocking,
        }
    return {
        "survey_id": survey.id,
        "questionnaire_id": questionnaire.id,
        "common_question_types": sorted(catalog.allowed_question_types(survey.collection_services)),
        "by_service": report,
    }
