"""Gestion du reseau d'enqueteurs : affectations, charge, productivite, synchronisation."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from apps.api.models import (
    Answer,
    Assignment,
    AssignmentStatus,
    EnumeratorProfile,
    Interview,
    InterviewStatus,
    Paradata,
    SampleUnit,
    Survey,
    SyncBatch,
    User,
    UserRole,
    utcnow,
)
from apps.api.services import collect as collect_service
from apps.api.services import collection_catalog as catalog


class FieldworkError(Exception):
    """Erreur fonctionnelle de gestion du terrain."""


# --------------------------------------------------------------------------
# Affectations
# --------------------------------------------------------------------------


def eligible_enumerators(session: Session, survey: Survey, service_code: str) -> list[User]:
    """Enqueteurs actifs, formes et habilites pour un mode de collecte donne."""
    rows = session.execute(
        select(User, EnumeratorProfile)
        .join(EnumeratorProfile, EnumeratorProfile.user_id == User.id)
        .where(
            User.org_id == survey.org_id,
            User.role == UserRole.ENUMERATOR.value,
            User.is_active.is_(True),
            EnumeratorProfile.status == "active",
        )
    ).all()

    eligible = []
    for user, profile in rows:
        certified = profile.certified_services or []
        # Une liste vide signifie « polyvalent ».
        if certified and service_code not in certified:
            continue
        if not profile.training_completed:
            continue
        eligible.append(user)
    return eligible


def current_load(session: Session, survey_id: str) -> dict[str, int]:
    """Nombre d'affectations ouvertes par enqueteur."""
    rows = session.execute(
        select(Assignment.enumerator_id, func.count(Assignment.id))
        .where(
            Assignment.survey_id == survey_id,
            Assignment.enumerator_id.is_not(None),
            Assignment.status.in_(
                [AssignmentStatus.ASSIGNED.value, AssignmentStatus.IN_PROGRESS.value]
            ),
        )
        .group_by(Assignment.enumerator_id)
    ).all()
    return {enum_id: count for enum_id, count in rows}


def auto_assign(
    session: Session,
    survey: Survey,
    service_code: str,
    *,
    limit: int | None = None,
    respect_zone: bool = True,
    due_in_days: int = 7,
) -> dict:
    """Repartit les unites non affectees entre les enqueteurs eligibles.

    Deux principes : la proximite geographique d'abord (un enqueteur travaille
    dans sa zone de rattachement), puis l'equilibrage de la charge en tenant
    compte de la capacite journaliere declaree de chacun.
    """
    service_code = service_code.upper()
    if not catalog.exists(service_code):
        raise FieldworkError(f"Service de collecte inconnu : {service_code}")
    if not catalog.get_service(service_code).capabilities.requires_enumerator:
        raise FieldworkError(f"Le mode {service_code} ne mobilise pas d'enqueteurs.")

    enumerators = eligible_enumerators(session, survey, service_code)
    if not enumerators:
        raise FieldworkError(
            "Aucun enqueteur eligible : verifier les habilitations et la formation."
        )

    profiles = {
        p.user_id: p
        for p in session.scalars(
            select(EnumeratorProfile).where(
                EnumeratorProfile.user_id.in_([u.id for u in enumerators])
            )
        ).all()
    }
    loads = current_load(session, survey.id)
    for user in enumerators:
        loads.setdefault(user.id, 0)

    pending = session.scalars(
        select(Assignment)
        .join(SampleUnit, Assignment.sample_unit_id == SampleUnit.id)
        .where(
            Assignment.survey_id == survey.id,
            Assignment.collection_service == service_code,
            Assignment.status == AssignmentStatus.PENDING.value,
        )
        .order_by(Assignment.priority, SampleUnit.geo_level_1)
        .limit(limit or 10_000)
    ).all()

    if not pending:
        return {"assigned": 0, "per_enumerator": {}, "unassigned": 0}

    by_zone: dict[str | None, list[User]] = defaultdict(list)
    for user in enumerators:
        profile = profiles.get(user.id)
        by_zone[profile.base_zone if profile else None].append(user)

    due = date.today() + timedelta(days=due_in_days)
    assigned_count = 0
    per_enumerator: dict[str, int] = defaultdict(int)
    unassigned = 0

    for assignment in pending:
        unit = session.get(SampleUnit, assignment.sample_unit_id)
        zone = unit.geo_level_1 if unit else None

        pool = by_zone.get(zone, []) if respect_zone else enumerators
        if not pool:
            pool = by_zone.get(None) or enumerators

        def remaining_capacity(user: User) -> int:
            profile = profiles.get(user.id)
            capacity = (profile.daily_capacity if profile else 8) * due_in_days
            return capacity - loads.get(user.id, 0)

        candidates = [u for u in pool if remaining_capacity(u) > 0]
        if not candidates:
            unassigned += 1
            continue

        chosen = min(candidates, key=lambda u: (loads.get(u.id, 0), u.id))
        assignment.enumerator_id = chosen.id
        assignment.status = AssignmentStatus.ASSIGNED.value
        assignment.due_date = assignment.due_date or due
        loads[chosen.id] = loads.get(chosen.id, 0) + 1
        per_enumerator[chosen.id] += 1
        assigned_count += 1

    session.flush()
    return {
        "assigned": assigned_count,
        "unassigned": unassigned,
        "per_enumerator": dict(per_enumerator),
        "enumerators_mobilised": len(per_enumerator),
    }


def reassign(session: Session, assignment_id: str, new_enumerator_id: str, reason: str) -> Assignment:
    """Transfere une affectation a un autre enqueteur en gardant la trace."""
    assignment = session.get(Assignment, assignment_id)
    if assignment is None:
        raise FieldworkError("Affectation introuvable.")
    if assignment.status == AssignmentStatus.DONE.value:
        raise FieldworkError("Une affectation terminee ne peut pas etre transferee.")

    previous = assignment.enumerator_id
    assignment.enumerator_id = new_enumerator_id
    assignment.status = AssignmentStatus.ASSIGNED.value
    note = f"[{utcnow():%Y-%m-%d %H:%M}] transfert de {previous or 'non affecte'} : {reason}"
    assignment.notes = f"{assignment.notes}\n{note}" if assignment.notes else note
    session.flush()
    return assignment


# --------------------------------------------------------------------------
# Suivi et productivite
# --------------------------------------------------------------------------


def enumerator_performance(session: Session, survey_id: str, since: date | None = None) -> list[dict]:
    """Tableau de bord de productivite et de qualite par enqueteur."""
    conditions = [Interview.survey_id == survey_id, Interview.enumerator_id.is_not(None)]
    if since is not None:
        conditions.append(Interview.started_at >= since)

    completed_statuses = [
        InterviewStatus.COMPLETED.value,
        InterviewStatus.SUBMITTED.value,
        InterviewStatus.VALIDATED.value,
    ]

    rows = session.execute(
        select(
            Interview.enumerator_id,
            User.full_name,
            func.count(Interview.id).label("total"),
            func.sum(case((Interview.status.in_(completed_statuses), 1), else_=0)).label("completed"),
            func.sum(case((Interview.status == InterviewStatus.REJECTED.value, 1), else_=0)).label(
                "rejected"
            ),
            func.sum(case((Interview.status == InterviewStatus.PARTIAL.value, 1), else_=0)).label(
                "partial"
            ),
            func.avg(Interview.duration_seconds).label("avg_duration"),
            func.avg(Interview.quality_score).label("avg_quality"),
        )
        .join(User, User.id == Interview.enumerator_id)
        .where(*conditions)
        .group_by(Interview.enumerator_id, User.full_name)
        .order_by(func.count(Interview.id).desc())
    ).all()

    loads = current_load(session, survey_id)
    performance = []
    for row in rows:
        total = row.total or 0
        completed = int(row.completed or 0)
        rejected = int(row.rejected or 0)
        performance.append(
            {
                "enumerator_id": row.enumerator_id,
                "full_name": row.full_name,
                "interviews_total": total,
                "interviews_completed": completed,
                "interviews_rejected": rejected,
                "interviews_partial": int(row.partial or 0),
                "completion_rate": round(completed / total, 3) if total else 0.0,
                "rejection_rate": round(rejected / total, 3) if total else 0.0,
                "avg_duration_minutes": round((row.avg_duration or 0) / 60, 1),
                "avg_quality_score": round(row.avg_quality or 0, 3),
                "open_assignments": loads.get(row.enumerator_id, 0),
            }
        )
    return performance


def survey_progress(session: Session, survey: Survey) -> dict:
    """Avancement global de la collecte, par mode et par statut."""
    by_status = dict(
        session.execute(
            select(Interview.status, func.count(Interview.id))
            .where(Interview.survey_id == survey.id)
            .group_by(Interview.status)
        ).all()
    )
    by_service = dict(
        session.execute(
            select(Interview.collection_service, func.count(Interview.id))
            .where(Interview.survey_id == survey.id)
            .group_by(Interview.collection_service)
        ).all()
    )
    by_assignment = dict(
        session.execute(
            select(Assignment.status, func.count(Assignment.id))
            .where(Assignment.survey_id == survey.id)
            .group_by(Assignment.status)
        ).all()
    )

    usable = sum(
        by_status.get(s, 0)
        for s in (
            InterviewStatus.COMPLETED.value,
            InterviewStatus.SUBMITTED.value,
            InterviewStatus.VALIDATED.value,
        )
    )
    target = survey.target_sample or 0
    return {
        "survey_id": survey.id,
        "target_sample": target,
        "usable_interviews": usable,
        "completion_rate": round(usable / target, 3) if target else None,
        "interviews_by_status": by_status,
        "interviews_by_service": by_service,
        "assignments_by_status": by_assignment,
        "quotas": [
            {
                "label": q.label,
                "dimensions": q.dimensions,
                "target": q.target,
                "achieved": q.achieved,
                "completion_rate": q.completion_rate,
                "is_full": q.is_full,
            }
            for q in survey.quotas
        ],
    }


def flagged_interviews(session: Session, survey_id: str, threshold: float = 0.6) -> list[dict]:
    """Entretiens a controler en priorite, tries par score de qualite croissant."""
    rows = session.scalars(
        select(Interview)
        .where(
            Interview.survey_id == survey_id,
            Interview.quality_score.is_not(None),
            Interview.quality_score < threshold,
            Interview.status.in_(
                [InterviewStatus.COMPLETED.value, InterviewStatus.SUBMITTED.value]
            ),
        )
        .order_by(Interview.quality_score)
        .limit(200)
    ).all()
    return [
        {
            "interview_id": i.id,
            "enumerator_id": i.enumerator_id,
            "collection_service": i.collection_service,
            "quality_score": i.quality_score,
            "quality_flags": i.quality_flags,
            "duration_seconds": i.duration_seconds,
            "started_at": i.started_at.isoformat() if i.started_at else None,
        }
        for i in rows
    ]


# --------------------------------------------------------------------------
# Synchronisation hors ligne (CAPI)
# --------------------------------------------------------------------------


def ingest_sync_batch(
    session: Session,
    survey: Survey,
    device_id: str,
    enumerator_id: str | None,
    interviews_payload: list[dict],
    app_version: str | None = None,
) -> dict:
    """Integre un lot d'entretiens collectes hors ligne.

    L'operation est idempotente grace a ``client_uuid`` : un lot rejoue apres
    une coupure reseau ne cree aucun doublon. Chaque entretien est traite
    independamment, une erreur unitaire n'invalide pas le lot.
    """
    batch = SyncBatch(
        device_id=device_id,
        enumerator_id=enumerator_id,
        survey_id=survey.id,
        received_at=utcnow(),
        interview_count=len(interviews_payload),
        app_version=app_version,
    )
    session.add(batch)
    session.flush()

    accepted = duplicates = rejected = 0
    errors: list[dict] = []

    for payload in interviews_payload:
        client_uuid = payload.get("client_uuid")
        if not client_uuid:
            rejected += 1
            errors.append({"client_uuid": None, "error": "client_uuid manquant"})
            continue

        existing = session.scalar(select(Interview).where(Interview.client_uuid == client_uuid))
        if existing is not None:
            duplicates += 1
            continue

        try:
            interview = collect_service.start_interview(
                session,
                survey,
                payload.get("collection_service", "CAPI"),
                enumerator_id=enumerator_id,
                assignment_id=payload.get("assignment_id"),
                client_uuid=client_uuid,
                language=payload.get("language"),
                device_id=device_id,
                latitude=payload.get("latitude"),
                longitude=payload.get("longitude"),
            )
            interview.sync_batch_id = batch.id
            interview.app_version = app_version
            interview.gps_accuracy_m = payload.get("gps_accuracy_m")
            if payload.get("started_at"):
                interview.started_at = _parse_dt(payload["started_at"]) or interview.started_at

            for code, value in (payload.get("answers") or {}).items():
                collect_service.save_answer(session, interview, code, value)

            for event in payload.get("paradata") or []:
                session.add(
                    Paradata(
                        interview_id=interview.id,
                        event_type=event.get("event_type", "unknown"),
                        question_code=event.get("question_code"),
                        occurred_at=_parse_dt(event.get("occurred_at")) or utcnow(),
                        duration_ms=event.get("duration_ms"),
                        payload=event.get("payload") or {},
                    )
                )

            collect_service.complete_interview(session, interview, allow_partial=True)
            interview.status = (
                InterviewStatus.SUBMITTED.value
                if interview.status == InterviewStatus.COMPLETED.value
                else interview.status
            )
            accepted += 1
        except Exception as exc:  # noqa: BLE001 - un echec unitaire ne bloque pas le lot
            rejected += 1
            errors.append({"client_uuid": client_uuid, "error": str(exc)})

    batch.accepted_count = accepted
    batch.duplicate_count = duplicates
    batch.rejected_count = rejected
    batch.errors = errors
    batch.status = "completed" if not rejected else "partial"
    session.flush()

    return {
        "batch_id": batch.id,
        "received": len(interviews_payload),
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": rejected,
        "errors": errors[:20],
    }


def _parse_dt(value):
    from datetime import datetime

    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def enumerator_workload(session: Session, enumerator_id: str) -> dict:
    """Vue individuelle : ce que l'enqueteur doit faire et ce qu'il a produit."""
    assignments = session.scalars(
        select(Assignment)
        .where(
            Assignment.enumerator_id == enumerator_id,
            Assignment.status.in_(
                [AssignmentStatus.ASSIGNED.value, AssignmentStatus.IN_PROGRESS.value]
            ),
        )
        .order_by(Assignment.priority, Assignment.due_date)
    ).all()

    done_today = session.scalar(
        select(func.count(Interview.id)).where(
            Interview.enumerator_id == enumerator_id,
            Interview.ended_at.is_not(None),
            func.date(Interview.ended_at) == date.today().isoformat(),
        )
    ) or 0

    answers_count = session.scalar(
        select(func.count(Answer.id))
        .join(Interview, Answer.interview_id == Interview.id)
        .where(Interview.enumerator_id == enumerator_id)
    ) or 0

    return {
        "enumerator_id": enumerator_id,
        "open_assignments": len(assignments),
        "completed_today": done_today,
        "answers_recorded": answers_count,
        "assignments": [
            {
                "id": a.id,
                "sample_unit_id": a.sample_unit_id,
                "collection_service": a.collection_service,
                "status": a.status,
                "priority": a.priority,
                "due_date": a.due_date.isoformat() if a.due_date else None,
                "attempts": a.attempts,
            }
            for a in assignments[:100]
        ],
    }
