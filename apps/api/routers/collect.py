"""Realisation de l'enquete : deroulement des entretiens, tous modes confondus."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.db import get_session
from apps.api.deps import current_user, get_survey, require_roles
from apps.api.models import Interview, Survey, User, UserRole
from apps.api.schemas import (
    AnswerBatch,
    AnswerIn,
    CompleteRequest,
    InterviewOut,
    StartInterviewRequest,
    SyncPayload,
    ValidateInterviewRequest,
)
from apps.api.services import collect as collect_service
from apps.api.services import fieldwork as fieldwork_service

router = APIRouter(prefix="/api/v1", tags=["Collecte"])


def _load_interview(session: Session, interview_id: str, user: User) -> Interview:
    interview = session.get(Interview, interview_id)
    if interview is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entretien introuvable.")
    survey = session.get(Survey, interview.survey_id)
    if survey is None or survey.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acces refuse.")
    # Un enqueteur ne voit que ses propres entretiens.
    if user.role == UserRole.ENUMERATOR.value and interview.enumerator_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Entretien d'un autre enqueteur.")
    return interview


@router.post(
    "/surveys/{survey_id}/interviews",
    response_model=InterviewOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ouvrir un entretien",
)
def start_interview(
    payload: StartInterviewRequest,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Interview:
    enumerator_id = user.id if user.role == UserRole.ENUMERATOR.value else None
    try:
        return collect_service.start_interview(
            session,
            survey,
            payload.collection_service,
            enumerator_id=enumerator_id or user.id,
            assignment_id=payload.assignment_id,
            client_uuid=payload.client_uuid,
            language=payload.language,
            device_id=payload.device_id,
            latitude=payload.latitude,
            longitude=payload.longitude,
        )
    except collect_service.CollectError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/interviews/{interview_id}", response_model=InterviewOut)
def get_interview(
    interview_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Interview:
    return _load_interview(session, interview_id, user)


@router.get("/interviews/{interview_id}/next", summary="Prochaines questions a poser")
def next_questions(
    interview_id: str,
    limit: int = 1,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Navigation dirigee par les regles de pertinence deja evaluees."""
    interview = _load_interview(session, interview_id, user)
    questions = collect_service.next_questions(session, interview, limit=min(limit, 50))
    return {
        "interview_id": interview.id,
        "questions": questions,
        "finished": not questions,
        "missing_required": collect_service.missing_required(session, interview),
    }


@router.post("/interviews/{interview_id}/answers", summary="Enregistrer une reponse")
def save_answer(
    interview_id: str,
    payload: AnswerIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    interview = _load_interview(session, interview_id, user)
    try:
        answer = collect_service.save_answer(
            session,
            interview,
            payload.question_code,
            payload.value,
            repeat_index=payload.repeat_index,
            duration_ms=payload.duration_ms,
        )
    except collect_service.AnswerRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"question_code": exc.question_code, "message": exc.message},
        ) from exc
    except collect_service.CollectError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return {
        "question_code": answer.question_code,
        "saved": True,
        "next": collect_service.next_questions(session, interview, limit=1),
    }


@router.post("/interviews/{interview_id}/answers/batch", summary="Enregistrer un lot de reponses")
def save_answers_batch(
    interview_id: str,
    payload: AnswerBatch,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Saisie par page (CAWI) ou rattrapage apres perte de reseau.

    Chaque reponse est validee independamment ; le lot renvoie le detail des
    rejets sans annuler les reponses acceptees.
    """
    interview = _load_interview(session, interview_id, user)
    saved, rejected = [], []
    for item in payload.answers:
        try:
            collect_service.save_answer(
                session,
                interview,
                item.question_code,
                item.value,
                repeat_index=item.repeat_index,
                duration_ms=item.duration_ms,
            )
            saved.append(item.question_code)
        except collect_service.AnswerRejected as exc:
            rejected.append({"question_code": exc.question_code, "message": exc.message})
        except collect_service.CollectError as exc:
            rejected.append({"question_code": item.question_code, "message": str(exc)})
    return {"saved": saved, "rejected": rejected, "saved_count": len(saved)}


@router.post("/interviews/{interview_id}/complete", summary="Cloturer un entretien")
def complete_interview(
    interview_id: str,
    payload: CompleteRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    interview = _load_interview(session, interview_id, user)
    try:
        collect_service.complete_interview(session, interview, allow_partial=payload.allow_partial)
    except collect_service.CollectError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {
        "interview_id": interview.id,
        "status": interview.status,
        "duration_seconds": interview.duration_seconds,
        "quality_score": interview.quality_score,
        "quality_flags": interview.quality_flags,
    }


@router.post("/interviews/{interview_id}/validate", summary="Valider ou rejeter apres controle")
def validate_interview(
    interview_id: str,
    payload: ValidateInterviewRequest,
    session: Session = Depends(get_session),
    user: User = Depends(require_roles(UserRole.SUPERVISOR)),
) -> dict:
    interview = _load_interview(session, interview_id, user)
    collect_service.validate_interview(
        session, interview, user.id, accepted=payload.accepted, notes=payload.notes
    )
    return {"interview_id": interview.id, "status": interview.status}


@router.get("/interviews/{interview_id}/answers", summary="Reponses d'un entretien")
def list_answers(
    interview_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    interview = _load_interview(session, interview_id, user)
    return {
        "interview_id": interview.id,
        "answers": {
            a.question_code: a.value
            for a in sorted(interview.answers, key=lambda a: (a.question_code, a.repeat_index))
        },
    }


# --------------------------------------------------------------------------
# Synchronisation hors ligne
# --------------------------------------------------------------------------


@router.post("/surveys/{survey_id}/sync", summary="Remonter un lot d'entretiens hors ligne")
def sync(
    payload: SyncPayload,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Point d'entree des tablettes CAPI : idempotent par ``client_uuid``."""
    return fieldwork_service.ingest_sync_batch(
        session,
        survey,
        device_id=payload.device_id,
        enumerator_id=user.id,
        interviews_payload=payload.interviews,
        app_version=payload.app_version,
    )


@router.get("/surveys/{survey_id}/sync/package", summary="Paquet a telecharger sur l'appareil")
def sync_package(
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Questionnaire publie et affectations de l'enqueteur, pour travail hors ligne."""
    from apps.api.services.questionnaire import to_schema

    questionnaire = survey.published_questionnaire
    if questionnaire is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Aucune version publiee du questionnaire."
        )
    workload = fieldwork_service.enumerator_workload(session, user.id)
    return {
        "survey": {
            "id": survey.id,
            "code": survey.code,
            "title": survey.title,
            "collection_services": survey.collection_services,
            "default_language": survey.default_language,
        },
        "questionnaire": to_schema(questionnaire),
        "schema_hash": questionnaire.schema_hash,
        "assignments": workload["assignments"],
    }


@router.get("/surveys/{survey_id}/interviews", summary="Lister les entretiens")
def list_interviews(
    status_filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    query = select(Interview).where(Interview.survey_id == survey.id)
    if status_filter:
        query = query.where(Interview.status == status_filter)
    if user.role == UserRole.ENUMERATOR.value:
        query = query.where(Interview.enumerator_id == user.id)

    rows = session.scalars(
        query.order_by(Interview.created_at.desc()).limit(min(limit, 500)).offset(offset)
    ).all()
    return {
        "count": len(rows),
        "interviews": [
            {
                "id": i.id,
                "collection_service": i.collection_service,
                "status": i.status,
                "enumerator_id": i.enumerator_id,
                "started_at": i.started_at.isoformat() if i.started_at else None,
                "duration_seconds": i.duration_seconds,
                "quality_score": i.quality_score,
            }
            for i in rows
        ],
    }
