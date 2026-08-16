"""Moteur de collecte : deroulement d'un entretien et enregistrement des reponses.

Ce module est partage par tous les services de collecte. Chaque mode (CAWI,
CAPI, CATI, PAPI, SMS) appelle les memes primitives ; seule l'enveloppe change
(navigateur, application hors ligne, plateau d'appels, saisie operateur).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.models import (
    Answer,
    Assignment,
    AssignmentStatus,
    Interview,
    InterviewStatus,
    Paradata,
    Question,
    Questionnaire,
    QuestionType,
    Quota,
    Section,
    Survey,
    utcnow,
)
from apps.api.services import collection_catalog as catalog
from apps.api.services.expressions import ExpressionError, evaluate, evaluate_bool


class CollectError(Exception):
    """Erreur fonctionnelle de collecte (regle metier non respectee)."""


class AnswerRejected(CollectError):
    """Une reponse viole une contrainte du questionnaire."""

    def __init__(self, question_code: str, message: str):
        self.question_code = question_code
        self.message = message
        super().__init__(f"{question_code} : {message}")


# --------------------------------------------------------------------------
# Ouverture d'un entretien
# --------------------------------------------------------------------------


def start_interview(
    session: Session,
    survey: Survey,
    collection_service: str,
    *,
    enumerator_id: str | None = None,
    assignment_id: str | None = None,
    client_uuid: str | None = None,
    language: str | None = None,
    device_id: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> Interview:
    """Ouvre un entretien sur la version publiee du questionnaire."""
    if survey.status not in {"active", "pilot"}:
        raise CollectError(f"L'enquete n'est pas ouverte a la collecte (statut : {survey.status}).")

    service_code = collection_service.upper()
    if not catalog.exists(service_code):
        raise CollectError(f"Service de collecte inconnu : {collection_service}")
    if service_code not in (survey.collection_services or []):
        raise CollectError(
            f"Le service {service_code} n'est pas active pour cette enquete. "
            f"Modes disponibles : {', '.join(survey.collection_services or [])}"
        )

    questionnaire = survey.published_questionnaire
    if questionnaire is None:
        raise CollectError("Aucune version publiee du questionnaire.")

    service = catalog.get_service(service_code)
    if service.capabilities.requires_enumerator and enumerator_id is None:
        raise CollectError(f"Le mode {service_code} exige un enqueteur identifie.")

    # Idempotence : un meme identifiant client ne cree jamais deux entretiens.
    if client_uuid:
        existing = session.scalar(select(Interview).where(Interview.client_uuid == client_uuid))
        if existing is not None:
            return existing

    interview = Interview(
        survey_id=survey.id,
        questionnaire_id=questionnaire.id,
        assignment_id=assignment_id,
        enumerator_id=enumerator_id,
        client_uuid=client_uuid,
        collection_service=service_code,
        status=InterviewStatus.IN_PROGRESS.value,
        language=language or survey.default_language,
        started_at=utcnow(),
        device_id=device_id,
        latitude=latitude,
        longitude=longitude,
    )
    session.add(interview)

    if assignment_id:
        assignment = session.get(Assignment, assignment_id)
        if assignment is not None:
            assignment.status = AssignmentStatus.IN_PROGRESS.value
            assignment.attempts += 1
            assignment.last_attempt_at = utcnow()

    session.flush()
    return interview


# --------------------------------------------------------------------------
# Contexte et navigation
# --------------------------------------------------------------------------


def build_context(session: Session, interview: Interview) -> dict[str, Any]:
    """Reconstitue {code_question: valeur} pour evaluer les expressions."""
    answers = session.scalars(
        select(Answer).where(Answer.interview_id == interview.id, Answer.repeat_index == 0)
    ).all()
    return {a.question_code: a.value for a in answers}


def _questions_of(session: Session, questionnaire_id: str) -> list[Question]:
    return list(
        session.scalars(
            select(Question)
            .join(Section, Question.section_id == Section.id)
            .where(Section.questionnaire_id == questionnaire_id)
            .order_by(Section.order_index, Question.order_index)
        ).all()
    )


def next_questions(session: Session, interview: Interview, limit: int = 1) -> list[dict]:
    """Retourne les prochaines questions pertinentes non encore renseignees.

    C'est le cœur de la navigation : les sauts de questions decoulent de
    l'evaluation des regles de pertinence sur les reponses deja fournies.
    """
    context = build_context(session, interview)
    answered = set(context)
    service = interview.collection_service
    result: list[dict] = []

    for question in _questions_of(session, interview.questionnaire_id):
        if question.code in answered:
            continue
        if question.only_services and service not in question.only_services:
            continue
        section = session.get(Section, question.section_id)
        if section is not None and not evaluate_bool(section.relevance, context):
            continue
        if not evaluate_bool(question.relevance, context):
            continue
        result.append(_render_question(question))
        if len(result) >= limit:
            break
    return result


def _render_question(question: Question) -> dict:
    payload = {
        "code": question.code,
        "type": question.type,
        "label": question.label,
        "hint": question.hint,
        "required": question.is_required,
        "min": question.min_value,
        "max": question.max_value,
        "allow_other": question.allow_other,
    }
    if question.choice_list is not None:
        payload["choices"] = [
            {"code": c.code, "label": c.label, "score": c.score, "exclusive": c.is_exclusive}
            for c in sorted(question.choice_list.choices, key=lambda c: c.order_index)
        ]
    return payload


# --------------------------------------------------------------------------
# Enregistrement d'une reponse
# --------------------------------------------------------------------------


def _coerce(question: Question, raw: Any) -> dict[str, Any]:
    """Range la valeur brute dans la bonne colonne typee."""
    slot: dict[str, Any] = {
        "value_text": None,
        "value_number": None,
        "value_date": None,
        "value_json": None,
    }
    if raw is None or raw == "":
        return slot

    q_type = QuestionType(question.type)

    if q_type in (QuestionType.INTEGER, QuestionType.DECIMAL, QuestionType.SCALE):
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise AnswerRejected(question.code, "Valeur numerique attendue.") from exc
        if q_type == QuestionType.INTEGER and number != int(number):
            raise AnswerRejected(question.code, "Nombre entier attendu.")
        slot["value_number"] = number
        slot["value_text"] = str(raw)

    elif q_type in (QuestionType.DATE, QuestionType.TIME):
        if isinstance(raw, datetime):
            slot["value_date"] = raw
        else:
            text = str(raw)
            parsed = None
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%H:%M:%S", "%H:%M"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise AnswerRejected(question.code, f"Format de date invalide : {text}")
            slot["value_date"] = parsed
        slot["value_text"] = str(raw)

    elif q_type == QuestionType.MULTI_CHOICE:
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        values = [str(v) for v in values]
        slot["value_json"] = values
        slot["value_text"] = " ".join(values)

    elif q_type == QuestionType.GEOPOINT:
        if not isinstance(raw, dict) or "lat" not in raw or "lon" not in raw:
            raise AnswerRejected(question.code, "Point GPS attendu sous la forme {lat, lon}.")
        slot["value_json"] = raw
        slot["value_text"] = f"{raw['lat']},{raw['lon']}"

    elif q_type in (QuestionType.PHOTO, QuestionType.AUDIO, QuestionType.FILE, QuestionType.SIGNATURE):
        slot["value_json"] = raw if isinstance(raw, dict) else {"uri": str(raw)}
        slot["value_text"] = slot["value_json"].get("uri")

    else:  # text, single_choice, barcode, note
        slot["value_text"] = str(raw)

    return slot


def _check_constraints(question: Question, raw: Any, context: dict) -> None:
    q_type = QuestionType(question.type)

    if question.is_required and (raw is None or raw == "" or raw == []):
        raise AnswerRejected(question.code, "Reponse obligatoire.")

    if raw is None or raw == "":
        return

    if q_type.is_choice and question.choice_list is not None:
        valid = {c.code for c in question.choice_list.choices}
        given = raw if isinstance(raw, (list, tuple)) else [raw]
        unknown = [str(v) for v in given if str(v) not in valid]
        if unknown and not question.allow_other:
            raise AnswerRejected(question.code, f"Modalite(s) inconnue(s) : {', '.join(unknown)}")
        if q_type == QuestionType.MULTI_CHOICE:
            exclusives = {c.code for c in question.choice_list.choices if c.is_exclusive}
            chosen = {str(v) for v in given}
            if chosen & exclusives and len(chosen) > 1:
                raise AnswerRejected(
                    question.code, "Une modalite exclusive ne peut pas etre combinee."
                )

    if q_type.is_numeric:
        number = float(raw)
        if question.min_value is not None and number < question.min_value:
            raise AnswerRejected(question.code, f"Valeur inferieure au minimum ({question.min_value}).")
        if question.max_value is not None and number > question.max_value:
            raise AnswerRejected(question.code, f"Valeur superieure au maximum ({question.max_value}).")

    if question.constraint_expr:
        local_context = dict(context)
        local_context["value"] = float(raw) if q_type.is_numeric else raw
        try:
            ok = bool(evaluate(question.constraint_expr, local_context))
        except ExpressionError as exc:
            raise AnswerRejected(question.code, f"Contrainte invalide : {exc}") from exc
        if not ok:
            message = (question.constraint_message or {}).get("fr") or "Valeur non conforme."
            raise AnswerRejected(question.code, message)


def save_answer(
    session: Session,
    interview: Interview,
    question_code: str,
    raw_value: Any,
    *,
    repeat_index: int = 0,
    duration_ms: int | None = None,
) -> Answer:
    """Valide puis enregistre une reponse ; ecrase la precedente si elle existe."""
    if interview.status not in {InterviewStatus.IN_PROGRESS.value, InterviewStatus.PARTIAL.value}:
        raise CollectError("L'entretien est cloture : la reponse ne peut plus etre modifiee.")

    question = session.scalar(
        select(Question)
        .join(Section, Question.section_id == Section.id)
        .where(Section.questionnaire_id == interview.questionnaire_id, Question.code == question_code)
    )
    if question is None:
        raise CollectError(f"Question inconnue dans cette version : {question_code}")

    if question.only_services and interview.collection_service not in question.only_services:
        raise CollectError(
            f"La question {question_code} n'est pas posee en mode {interview.collection_service}."
        )

    context = build_context(session, interview)
    _check_constraints(question, raw_value, context)
    slot = _coerce(question, raw_value)

    existing = session.scalar(
        select(Answer).where(
            Answer.interview_id == interview.id,
            Answer.question_code == question_code,
            Answer.repeat_index == repeat_index,
        )
    )
    if existing is not None:
        for key, value in slot.items():
            setattr(existing, key, value)
        existing.answered_at = utcnow()
        answer = existing
        _log_event(session, interview, "answer_changed", question_code, duration_ms)
    else:
        answer = Answer(
            interview_id=interview.id,
            question_id=question.id,
            question_code=question_code,
            question_type=question.type,
            repeat_index=repeat_index,
            answered_at=utcnow(),
            **slot,
        )
        session.add(answer)
        _log_event(session, interview, "answer_saved", question_code, duration_ms)

    session.flush()
    _recompute_calculations(session, interview)
    return answer


def _recompute_calculations(session: Session, interview: Interview) -> None:
    """Recalcule les questions de type ``calculate`` apres chaque saisie."""
    context = build_context(session, interview)
    for question in _questions_of(session, interview.questionnaire_id):
        if question.type != QuestionType.CALCULATE.value or not question.calculation:
            continue
        try:
            value = evaluate(question.calculation, context)
        except ExpressionError:
            continue
        if value is None:
            continue
        existing = session.scalar(
            select(Answer).where(
                Answer.interview_id == interview.id,
                Answer.question_code == question.code,
                Answer.repeat_index == 0,
            )
        )
        number = float(value) if isinstance(value, (int, float)) else None
        if existing is None:
            session.add(
                Answer(
                    interview_id=interview.id,
                    question_id=question.id,
                    question_code=question.code,
                    question_type=question.type,
                    value_number=number,
                    value_text=str(value),
                    answered_at=utcnow(),
                )
            )
        else:
            existing.value_number = number
            existing.value_text = str(value)
        context[question.code] = value
    session.flush()


def _log_event(
    session: Session,
    interview: Interview,
    event_type: str,
    question_code: str | None = None,
    duration_ms: int | None = None,
    payload: dict | None = None,
) -> None:
    session.add(
        Paradata(
            interview_id=interview.id,
            event_type=event_type,
            question_code=question_code,
            occurred_at=utcnow(),
            duration_ms=duration_ms,
            payload=payload or {},
        )
    )


# --------------------------------------------------------------------------
# Cloture, quotas et qualite
# --------------------------------------------------------------------------


def missing_required(session: Session, interview: Interview) -> list[str]:
    """Questions obligatoires pertinentes restees sans reponse."""
    context = build_context(session, interview)
    answered = set(context)
    missing: list[str] = []
    for question in _questions_of(session, interview.questionnaire_id):
        if not question.is_required or question.code in answered:
            continue
        if question.only_services and interview.collection_service not in question.only_services:
            continue
        section = session.get(Section, question.section_id)
        if section is not None and not evaluate_bool(section.relevance, context):
            continue
        if evaluate_bool(question.relevance, context):
            missing.append(question.code)
    return missing


def compute_quality(session: Session, interview: Interview) -> tuple[float, list[str]]:
    """Score de qualite de 0 a 1 et anomalies detectees.

    Les criteres sont ceux du controle terrain classique : entretien trop
    rapide, position GPS absente, taux de non-reponse eleve, corrections
    excessives, uniformite suspecte des reponses a echelle.
    """
    flags: list[str] = []
    score = 1.0
    service = catalog.get_service(interview.collection_service)

    expected_seconds = service.typical_duration_minutes * 60
    if interview.duration_seconds is not None:
        if interview.duration_seconds < expected_seconds * 0.25:
            flags.append("duree_anormalement_courte")
            score -= 0.35
        elif interview.duration_seconds > expected_seconds * 4:
            flags.append("duree_anormalement_longue")
            score -= 0.10

    if service.capabilities.geolocation and interview.latitude is None:
        flags.append("gps_absent")
        score -= 0.20

    total_questions = len(_questions_of(session, interview.questionnaire_id))
    answered = session.scalar(
        select(func.count(Answer.id)).where(Answer.interview_id == interview.id)
    ) or 0
    if total_questions and answered / total_questions < 0.5:
        flags.append("taux_de_remplissage_faible")
        score -= 0.20

    changes = session.scalar(
        select(func.count(Paradata.id)).where(
            Paradata.interview_id == interview.id, Paradata.event_type == "answer_changed"
        )
    ) or 0
    if answered and changes > answered:
        flags.append("corrections_excessives")
        score -= 0.10

    # Reponses a echelle toutes identiques : signe de remplissage mecanique.
    scale_values = session.scalars(
        select(Answer.value_number).where(
            Answer.interview_id == interview.id,
            Answer.question_type == QuestionType.SCALE.value,
            Answer.value_number.is_not(None),
        )
    ).all()
    if len(scale_values) >= 5 and len(set(scale_values)) == 1:
        flags.append("reponses_uniformes")
        score -= 0.15

    return max(0.0, round(score, 3)), flags


def complete_interview(
    session: Session, interview: Interview, *, allow_partial: bool = False
) -> Interview:
    """Cloture l'entretien : controle de completude, qualite et quotas."""
    if interview.status not in {InterviewStatus.IN_PROGRESS.value, InterviewStatus.PARTIAL.value}:
        raise CollectError("Entretien deja cloture.")

    missing = missing_required(session, interview)
    if missing and not allow_partial:
        raise CollectError("Questions obligatoires sans reponse : " + ", ".join(missing[:10]))

    now = utcnow()
    interview.ended_at = now
    interview.submitted_at = now
    if interview.started_at:
        interview.duration_seconds = int((now - interview.started_at).total_seconds())
    interview.status = (
        InterviewStatus.PARTIAL.value if missing else InterviewStatus.COMPLETED.value
    )

    score, flags = compute_quality(session, interview)
    interview.quality_score = score
    interview.quality_flags = flags

    if interview.assignment_id:
        assignment = session.get(Assignment, interview.assignment_id)
        if assignment is not None:
            assignment.status = AssignmentStatus.DONE.value
            assignment.outcome_code = interview.status

    if not missing:
        _increment_quotas(session, interview)

    _log_event(session, interview, "interview_completed", payload={"quality": score, "flags": flags})
    session.flush()
    return interview


def _increment_quotas(session: Session, interview: Interview) -> None:
    """Incremente les quotas dont le croisement correspond a l'entretien."""
    context = build_context(session, interview)
    quotas = session.scalars(select(Quota).where(Quota.survey_id == interview.survey_id)).all()
    for quota in quotas:
        if not quota.dimensions:
            continue
        if all(str(context.get(key)) == str(value) for key, value in quota.dimensions.items()):
            quota.achieved += 1


def quota_state(session: Session, survey_id: str, context: dict) -> dict:
    """Indique si un profil de repondant est encore ouvert a la collecte."""
    quotas = session.scalars(select(Quota).where(Quota.survey_id == survey_id)).all()
    blocked = []
    for quota in quotas:
        if not quota.dimensions:
            continue
        matches = all(str(context.get(k)) == str(v) for k, v in quota.dimensions.items())
        if matches and quota.is_full and quota.is_blocking:
            blocked.append(quota.label)
    return {"open": not blocked, "blocked_by": blocked}


def validate_interview(
    session: Session,
    interview: Interview,
    supervisor_id: str,
    *,
    accepted: bool,
    notes: str | None = None,
) -> Interview:
    """Decision du superviseur apres controle qualite."""
    interview.status = (
        InterviewStatus.VALIDATED.value if accepted else InterviewStatus.REJECTED.value
    )
    interview.validated_by = supervisor_id
    interview.validated_at = utcnow()
    interview.validation_notes = notes

    if not accepted and interview.assignment_id:
        # Un entretien rejete rouvre l'affectation pour un nouveau passage.
        assignment = session.get(Assignment, interview.assignment_id)
        if assignment is not None:
            assignment.status = AssignmentStatus.ASSIGNED.value

    _log_event(session, interview, "interview_validated" if accepted else "interview_rejected")
    session.flush()
    return interview
