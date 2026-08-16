"""Gestion des enqueteurs et pilotage du terrain."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.db import get_session
from apps.api.deps import current_user, get_survey, require_roles
from apps.api.models import (
    Assignment,
    EnumeratorProfile,
    Survey,
    SyncBatch,
    User,
    UserRole,
)
from apps.api.schemas import (
    AutoAssignRequest,
    EnumeratorCreate,
    EnumeratorOut,
    ReassignRequest,
)
from apps.api.security import hash_password
from apps.api.services import collection_catalog as catalog
from apps.api.services import fieldwork as fw

router = APIRouter(prefix="/api/v1", tags=["Terrain et enqueteurs"])

_SUPERVISORS = require_roles(UserRole.SUPERVISOR)


# --------------------------------------------------------------------------
# Reseau d'enqueteurs
# --------------------------------------------------------------------------


@router.post("/enumerators", response_model=EnumeratorOut, status_code=status.HTTP_201_CREATED)
def create_enumerator(
    payload: EnumeratorCreate,
    session: Session = Depends(get_session),
    user: User = Depends(_SUPERVISORS),
) -> EnumeratorOut:
    """Enrole un enqueteur et cree son profil terrain."""
    email = payload.email.lower().strip()
    if session.scalar(select(User).where(User.org_id == user.org_id, User.email == email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cet e-mail est deja enregistre.")

    for code in payload.certified_services:
        if not catalog.exists(code):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Service de collecte inconnu : {code}",
            )

    account = User(
        org_id=user.org_id,
        email=email,
        full_name=payload.full_name,
        phone=payload.phone,
        role=UserRole.ENUMERATOR.value,
        password_hash=hash_password(payload.password),
    )
    session.add(account)
    session.flush()

    profile = EnumeratorProfile(
        user_id=account.id,
        supervisor_id=payload.supervisor_id or user.id,
        matricule=payload.matricule,
        base_zone=payload.base_zone,
        daily_capacity=payload.daily_capacity,
        certified_services=[c.upper() for c in payload.certified_services],
        training_completed=payload.training_completed,
    )
    session.add(profile)
    session.flush()

    return EnumeratorOut(
        user_id=account.id,
        full_name=account.full_name,
        email=account.email,
        matricule=profile.matricule,
        base_zone=profile.base_zone,
        daily_capacity=profile.daily_capacity,
        certified_services=profile.certified_services,
        training_completed=profile.training_completed,
        status=profile.status,
    )


@router.get("/enumerators", response_model=list[EnumeratorOut])
def list_enumerators(
    zone: str | None = None,
    service: str | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[EnumeratorOut]:
    rows = session.execute(
        select(User, EnumeratorProfile)
        .join(EnumeratorProfile, EnumeratorProfile.user_id == User.id)
        .where(User.org_id == user.org_id, User.role == UserRole.ENUMERATOR.value)
        .order_by(EnumeratorProfile.matricule)
    ).all()

    result = []
    for account, profile in rows:
        if zone and profile.base_zone != zone:
            continue
        certified = profile.certified_services or []
        if service and certified and service.upper() not in certified:
            continue
        result.append(
            EnumeratorOut(
                user_id=account.id,
                full_name=account.full_name,
                email=account.email,
                matricule=profile.matricule,
                base_zone=profile.base_zone,
                daily_capacity=profile.daily_capacity,
                certified_services=certified,
                training_completed=profile.training_completed,
                status=profile.status,
            )
        )
    return result


@router.patch("/enumerators/{user_id}", summary="Mettre a jour un profil enqueteur")
def update_enumerator(
    user_id: str,
    training_completed: bool | None = None,
    daily_capacity: int | None = None,
    base_zone: str | None = None,
    profile_status: str | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(_SUPERVISORS),
) -> dict:
    profile = session.scalar(select(EnumeratorProfile).where(EnumeratorProfile.user_id == user_id))
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profil enqueteur introuvable.")

    if training_completed is not None:
        profile.training_completed = training_completed
    if daily_capacity is not None:
        profile.daily_capacity = daily_capacity
    if base_zone is not None:
        profile.base_zone = base_zone
    if profile_status is not None:
        profile.status = profile_status
    session.flush()
    return {"user_id": user_id, "updated": True, "status": profile.status}


@router.get("/enumerators/me/workload", summary="Charge de travail de l'enqueteur connecte")
def my_workload(
    session: Session = Depends(get_session), user: User = Depends(current_user)
) -> dict:
    return fw.enumerator_workload(session, user.id)


# --------------------------------------------------------------------------
# Affectations
# --------------------------------------------------------------------------


@router.post("/surveys/{survey_id}/assignments/auto", summary="Affectation automatique")
def auto_assign(
    payload: AutoAssignRequest,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(_SUPERVISORS),
) -> dict:
    """Repartit l'echantillon en attente selon la zone puis la charge."""
    try:
        return fw.auto_assign(
            session,
            survey,
            payload.collection_service,
            limit=payload.limit,
            respect_zone=payload.respect_zone,
            due_in_days=payload.due_in_days,
        )
    except fw.FieldworkError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/assignments/{assignment_id}/reassign", summary="Transferer une affectation")
def reassign(
    assignment_id: str,
    payload: ReassignRequest,
    session: Session = Depends(get_session),
    user: User = Depends(_SUPERVISORS),
) -> dict:
    try:
        assignment = fw.reassign(session, assignment_id, payload.new_enumerator_id, payload.reason)
    except fw.FieldworkError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"assignment_id": assignment.id, "enumerator_id": assignment.enumerator_id}


@router.get("/surveys/{survey_id}/assignments", summary="Lister les affectations")
def list_assignments(
    status_filter: str | None = None,
    enumerator_id: str | None = None,
    limit: int = 200,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    query = select(Assignment).where(Assignment.survey_id == survey.id)
    if status_filter:
        query = query.where(Assignment.status == status_filter)
    if enumerator_id:
        query = query.where(Assignment.enumerator_id == enumerator_id)
    if user.role == UserRole.ENUMERATOR.value:
        query = query.where(Assignment.enumerator_id == user.id)

    rows = session.scalars(
        query.order_by(Assignment.priority, Assignment.due_date).limit(min(limit, 1000))
    ).all()
    return {
        "count": len(rows),
        "assignments": [
            {
                "id": a.id,
                "sample_unit_id": a.sample_unit_id,
                "enumerator_id": a.enumerator_id,
                "collection_service": a.collection_service,
                "status": a.status,
                "priority": a.priority,
                "attempts": a.attempts,
                "due_date": a.due_date.isoformat() if a.due_date else None,
            }
            for a in rows
        ],
    }


# --------------------------------------------------------------------------
# Pilotage
# --------------------------------------------------------------------------


@router.get("/surveys/{survey_id}/progress", summary="Avancement de la collecte")
def progress(
    survey: Survey = Depends(get_survey), session: Session = Depends(get_session)
) -> dict:
    return fw.survey_progress(session, survey)


@router.get("/surveys/{survey_id}/performance", summary="Productivite des enqueteurs")
def performance(
    since: date | None = None,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(_SUPERVISORS),
) -> dict:
    return {"survey_id": survey.id, "enumerators": fw.enumerator_performance(session, survey.id, since)}


@router.get("/surveys/{survey_id}/quality/flagged", summary="Entretiens a controler")
def flagged(
    threshold: float = 0.6,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(_SUPERVISORS),
) -> dict:
    rows = fw.flagged_interviews(session, survey.id, threshold=threshold)
    return {"threshold": threshold, "count": len(rows), "interviews": rows}


@router.get("/surveys/{survey_id}/sync/batches", summary="Historique des synchronisations")
def sync_batches(
    limit: int = 50,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(_SUPERVISORS),
) -> dict:
    rows = session.scalars(
        select(SyncBatch)
        .where(SyncBatch.survey_id == survey.id)
        .order_by(SyncBatch.received_at.desc())
        .limit(min(limit, 200))
    ).all()
    return {
        "count": len(rows),
        "batches": [
            {
                "id": b.id,
                "device_id": b.device_id,
                "enumerator_id": b.enumerator_id,
                "received_at": b.received_at.isoformat(),
                "interview_count": b.interview_count,
                "accepted": b.accepted_count,
                "duplicates": b.duplicate_count,
                "rejected": b.rejected_count,
                "status": b.status,
            }
            for b in rows
        ],
    }
