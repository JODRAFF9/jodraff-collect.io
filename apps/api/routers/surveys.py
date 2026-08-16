"""Enquetes : creation depuis le catalogue, cycle de vie, quotas, echantillon."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.db import get_session
from apps.api.deps import current_user, get_survey, require_roles
from apps.api.models import (
    Assignment,
    AssignmentStatus,
    Questionnaire,
    Quota,
    SampleUnit,
    Survey,
    SurveyStatus,
    User,
    UserRole,
)
from apps.api.schemas import (
    QuotaIn,
    SampleImport,
    SurveyCreate,
    SurveyOut,
    SurveyUpdate,
)
from apps.api.services import collection_catalog as catalog
from apps.api.services import fieldwork as fieldwork_service

router = APIRouter(prefix="/api/v1/surveys", tags=["Enquetes"])

_DESIGNERS = require_roles(UserRole.METHODOLOGIST, UserRole.SUPERVISOR)


@router.post("", response_model=SurveyOut, status_code=status.HTTP_201_CREATED)
def create_survey(
    payload: SurveyCreate,
    session: Session = Depends(get_session),
    user: User = Depends(_DESIGNERS),
) -> Survey:
    """Cree une enquete a partir des services de collecte choisis a l'entree."""
    exists = session.scalar(
        select(Survey).where(Survey.org_id == user.org_id, Survey.code == payload.code)
    )
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Le code {payload.code} est deja utilise."
        )

    survey = Survey(
        org_id=user.org_id,
        code=payload.code,
        title=payload.title,
        description=payload.description,
        status=SurveyStatus.DESIGN.value,
        collection_services=payload.collection_services,
        target_sample=payload.target_sample,
        start_date=payload.start_date,
        end_date=payload.end_date,
        default_language=payload.default_language,
        languages=payload.languages,
        created_by=user.id,
    )
    session.add(survey)
    session.flush()

    # Une enquete nait avec un questionnaire brouillon en version 1.
    session.add(
        Questionnaire(survey_id=survey.id, version=1, title=payload.title, settings={})
    )
    session.flush()
    return survey


@router.get("", response_model=list[SurveyOut])
def list_surveys(
    status_filter: str | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[Survey]:
    query = select(Survey).where(Survey.org_id == user.org_id)
    if status_filter:
        query = query.where(Survey.status == status_filter)
    return list(session.scalars(query.order_by(Survey.created_at.desc())).all())


@router.get("/{survey_id}", response_model=SurveyOut)
def get_survey_detail(survey: Survey = Depends(get_survey)) -> Survey:
    return survey


@router.patch("/{survey_id}", response_model=SurveyOut)
def update_survey(
    payload: SurveyUpdate,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(_DESIGNERS),
) -> Survey:
    data = payload.model_dump(exclude_unset=True)

    if "status" in data:
        new_status = data["status"]
        valid = {s.value for s in SurveyStatus}
        if new_status not in valid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Statut invalide. Valeurs possibles : {', '.join(sorted(valid))}",
            )
        # Ouvrir la collecte exige un questionnaire publie.
        if new_status in {SurveyStatus.ACTIVE.value, SurveyStatus.PILOT.value}:
            if survey.published_questionnaire is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Publier une version du questionnaire avant d'ouvrir la collecte.",
                )

    for key, value in data.items():
        setattr(survey, key, value)
    session.flush()
    return survey


@router.get("/{survey_id}/overview", summary="Tableau de bord de l'enquete")
def survey_overview(
    survey: Survey = Depends(get_survey), session: Session = Depends(get_session)
) -> dict:
    progress = fieldwork_service.survey_progress(session, survey)
    services = [catalog.get_service(code).to_dict() for code in survey.collection_services]
    questionnaire = survey.published_questionnaire
    return {
        "survey": {
            "id": survey.id,
            "code": survey.code,
            "title": survey.title,
            "status": survey.status,
            "collection_services": survey.collection_services,
        },
        "questionnaire": (
            {
                "id": questionnaire.id,
                "version": questionnaire.version,
                "schema_hash": questionnaire.schema_hash,
            }
            if questionnaire
            else None
        ),
        "services": services,
        "progress": progress,
    }


# --------------------------------------------------------------------------
# Quotas
# --------------------------------------------------------------------------


@router.post("/{survey_id}/quotas", status_code=status.HTTP_201_CREATED)
def add_quota(
    payload: QuotaIn,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(_DESIGNERS),
) -> dict:
    quota = Quota(
        survey_id=survey.id,
        label=payload.label,
        dimensions=payload.dimensions,
        target=payload.target,
        is_blocking=payload.is_blocking,
    )
    session.add(quota)
    session.flush()
    return {"id": quota.id, "label": quota.label, "target": quota.target}


@router.get("/{survey_id}/quotas")
def list_quotas(survey: Survey = Depends(get_survey)) -> list[dict]:
    return [
        {
            "id": q.id,
            "label": q.label,
            "dimensions": q.dimensions,
            "target": q.target,
            "achieved": q.achieved,
            "completion_rate": q.completion_rate,
            "is_full": q.is_full,
            "is_blocking": q.is_blocking,
        }
        for q in survey.quotas
    ]


# --------------------------------------------------------------------------
# Echantillon
# --------------------------------------------------------------------------


@router.post("/{survey_id}/sample", status_code=status.HTTP_201_CREATED)
def import_sample(
    payload: SampleImport,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
    user: User = Depends(require_roles(UserRole.SUPERVISOR, UserRole.METHODOLOGIST)),
) -> dict:
    """Charge l'echantillon et cree une affectation en attente par unite."""
    if payload.collection_service not in survey.collection_services:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Le mode {payload.collection_service} n'est pas active sur cette enquete.",
        )

    existing_codes = set(
        session.scalars(select(SampleUnit.code).where(SampleUnit.survey_id == survey.id)).all()
    )

    created = skipped = 0
    for unit_in in payload.units:
        if unit_in.code in existing_codes:
            skipped += 1
            continue
        unit = SampleUnit(survey_id=survey.id, **unit_in.model_dump())
        session.add(unit)
        session.flush()
        session.add(
            Assignment(
                survey_id=survey.id,
                sample_unit_id=unit.id,
                collection_service=payload.collection_service,
                status=AssignmentStatus.PENDING.value,
            )
        )
        existing_codes.add(unit_in.code)
        created += 1

    session.flush()
    return {"created": created, "skipped_duplicates": skipped, "total_submitted": len(payload.units)}


@router.get("/{survey_id}/sample")
def list_sample(
    limit: int = 100,
    offset: int = 0,
    survey: Survey = Depends(get_survey),
    session: Session = Depends(get_session),
) -> dict:
    units = session.scalars(
        select(SampleUnit)
        .where(SampleUnit.survey_id == survey.id)
        .order_by(SampleUnit.code)
        .limit(min(limit, 500))
        .offset(offset)
    ).all()
    return {
        "count": len(units),
        "units": [
            {
                "id": u.id,
                "code": u.code,
                "contact_name": u.contact_name,
                "geo_level_1": u.geo_level_1,
                "geo_level_2": u.geo_level_2,
                "stratum": u.stratum,
                "sampling_weight": u.sampling_weight,
            }
            for u in units
        ],
    }
