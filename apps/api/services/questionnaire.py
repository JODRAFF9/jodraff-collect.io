"""Conception des questionnaires : import/export, validation et publication."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from apps.api.models import (
    Choice,
    ChoiceList,
    Question,
    Questionnaire,
    QuestionnaireStatus,
    QuestionType,
    Section,
    Survey,
    utcnow,
)
from apps.api.services import collection_catalog as catalog
from apps.api.services.expressions import referenced_variables, validate_expression


class ValidationIssue:
    """Anomalie detectee lors du controle d'un questionnaire."""

    __slots__ = ("severity", "location", "message")

    def __init__(self, severity: str, location: str, message: str):
        self.severity = severity  # "error" | "warning"
        self.location = location
        self.message = message

    def to_dict(self) -> dict:
        return {"severity": self.severity, "location": self.location, "message": self.message}

    def __repr__(self) -> str:  # pragma: no cover - confort de debogage
        return f"<{self.severity} {self.location}: {self.message}>"


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def to_schema(questionnaire: Questionnaire) -> dict[str, Any]:
    """Exporte le questionnaire sous forme de dictionnaire portable.

    Ce format est celui consomme par l'application terrain, l'export XLSForm
    et la generation de la maquette papier PAPI.
    """
    return {
        "id": questionnaire.id,
        "survey_id": questionnaire.survey_id,
        "version": questionnaire.version,
        "title": questionnaire.title,
        "status": questionnaire.status,
        "settings": questionnaire.settings or {},
        "choice_lists": [
            {
                "code": cl.code,
                "name": cl.name,
                "choices": [
                    {
                        "code": c.code,
                        "label": c.label,
                        "order": c.order_index,
                        "score": c.score,
                        "is_exclusive": c.is_exclusive,
                    }
                    for c in sorted(cl.choices, key=lambda c: c.order_index)
                ],
            }
            for cl in sorted(questionnaire.choice_lists, key=lambda c: c.code)
        ],
        "sections": [
            {
                "code": s.code,
                "label": s.label,
                "description": s.description,
                "order": s.order_index,
                "is_repeatable": s.is_repeatable,
                "repeat_count_question": s.repeat_count_question,
                "relevance": s.relevance,
                "questions": [
                    {
                        "code": q.code,
                        "type": q.type,
                        "label": q.label,
                        "hint": q.hint,
                        "order": q.order_index,
                        "required": q.is_required,
                        "relevance": q.relevance,
                        "constraint": q.constraint_expr,
                        "constraint_message": q.constraint_message,
                        "calculation": q.calculation,
                        "min": q.min_value,
                        "max": q.max_value,
                        "choice_list": q.choice_list.code if q.choice_list else None,
                        "allow_other": q.allow_other,
                        "only_services": q.only_services or [],
                        "is_pii": q.is_pii,
                        "analysis_role": q.analysis_role,
                    }
                    for q in sorted(s.questions, key=lambda q: q.order_index)
                ],
            }
            for s in sorted(questionnaire.sections, key=lambda s: s.order_index)
        ],
    }


def schema_hash(schema: dict) -> str:
    """Empreinte stable du schema, pour detecter les divergences de version."""
    payload = json.dumps(schema, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def from_schema(session: Session, questionnaire: Questionnaire, schema: dict) -> Questionnaire:
    """Remplace le contenu d'un questionnaire brouillon par un schema importe."""
    if not questionnaire.is_editable:
        raise ValueError("Un questionnaire publie ne peut pas etre modifie : creer une version.")

    for section in list(questionnaire.sections):
        session.delete(section)
    for choice_list in list(questionnaire.choice_lists):
        session.delete(choice_list)
    session.flush()

    questionnaire.title = schema.get("title", questionnaire.title)
    questionnaire.settings = schema.get("settings", {})

    lists_by_code: dict[str, ChoiceList] = {}
    for raw_list in schema.get("choice_lists", []):
        choice_list = ChoiceList(
            questionnaire_id=questionnaire.id,
            code=raw_list["code"],
            name=raw_list.get("name", raw_list["code"]),
        )
        session.add(choice_list)
        session.flush()
        for index, raw_choice in enumerate(raw_list.get("choices", [])):
            session.add(
                Choice(
                    choice_list_id=choice_list.id,
                    code=str(raw_choice["code"]),
                    label=_as_label(raw_choice.get("label")),
                    order_index=raw_choice.get("order", index),
                    score=raw_choice.get("score"),
                    is_exclusive=raw_choice.get("is_exclusive", False),
                )
            )
        lists_by_code[choice_list.code] = choice_list

    for s_index, raw_section in enumerate(schema.get("sections", [])):
        section = Section(
            questionnaire_id=questionnaire.id,
            code=raw_section["code"],
            label=_as_label(raw_section.get("label")),
            description=_as_label(raw_section.get("description")) if raw_section.get("description") else {},
            order_index=raw_section.get("order", s_index),
            is_repeatable=raw_section.get("is_repeatable", False),
            repeat_count_question=raw_section.get("repeat_count_question"),
            relevance=raw_section.get("relevance"),
        )
        session.add(section)
        session.flush()

        for q_index, raw_question in enumerate(raw_section.get("questions", [])):
            list_code = raw_question.get("choice_list")
            session.add(
                Question(
                    section_id=section.id,
                    code=raw_question["code"],
                    type=raw_question["type"],
                    label=_as_label(raw_question.get("label")),
                    hint=_as_label(raw_question.get("hint")) if raw_question.get("hint") else {},
                    order_index=raw_question.get("order", q_index),
                    is_required=raw_question.get("required", False),
                    relevance=raw_question.get("relevance"),
                    constraint_expr=raw_question.get("constraint"),
                    constraint_message=_as_label(raw_question.get("constraint_message") or None) or {},
                    calculation=raw_question.get("calculation"),
                    min_value=raw_question.get("min"),
                    max_value=raw_question.get("max"),
                    choice_list_id=lists_by_code[list_code].id if list_code else None,
                    allow_other=raw_question.get("allow_other", False),
                    only_services=raw_question.get("only_services", []),
                    is_pii=raw_question.get("is_pii", False),
                    analysis_role=raw_question.get("analysis_role"),
                )
            )

    session.flush()
    # Les sections et listes ont ete creees par cle etrangere directe : on
    # invalide le cache d'identite pour que les collections soient rechargees.
    session.expire(questionnaire, ["sections", "choice_lists"])
    return questionnaire


def _as_label(value: Any) -> dict:
    """Accepte une chaine simple ou un dictionnaire multilingue."""
    if value is None:
        return {}
    if isinstance(value, str):
        return {"fr": value}
    return dict(value)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate(questionnaire: Questionnaire, survey: Survey) -> list[ValidationIssue]:
    """Controle complet avant publication.

    Verifie la coherence interne (codes, types, expressions, ordre des
    references) et la compatibilite avec les services de collecte retenus
    pour l'enquete.
    """
    issues: list[ValidationIssue] = []
    services = survey.collection_services or []
    allowed_types = catalog.allowed_question_types(services)

    questions = list(questionnaire.iter_questions())
    if not questions:
        issues.append(ValidationIssue("error", "questionnaire", "Le questionnaire ne contient aucune question."))
        return issues

    codes: list[str] = []
    seen_codes: set[str] = set()
    list_codes = {cl.code for cl in questionnaire.choice_lists}

    for question in questions:
        location = f"question:{question.code}"

        if question.code in seen_codes:
            issues.append(ValidationIssue("error", location, "Code de question duplique."))
        seen_codes.add(question.code)
        codes.append(question.code)

        if not question.code.isidentifier():
            issues.append(
                ValidationIssue(
                    "error",
                    location,
                    "Le code doit etre un identifiant valide (lettres, chiffres, underscore, "
                    "sans commencer par un chiffre) pour etre utilisable en SQL et dans Power BI.",
                )
            )

        try:
            q_type = QuestionType(question.type)
        except ValueError:
            issues.append(ValidationIssue("error", location, f"Type inconnu : {question.type}"))
            continue

        if not question.label:
            issues.append(ValidationIssue("error", location, "Libelle manquant."))
        elif survey.default_language not in question.label:
            issues.append(
                ValidationIssue(
                    "warning",
                    location,
                    f"Libelle absent dans la langue par defaut ({survey.default_language}).",
                )
            )

        # Compatibilite avec les modes de collecte choisis a l'entree.
        restricted_to = question.only_services or []
        applicable = restricted_to or services
        if question.type not in allowed_types and not restricted_to:
            incompatible = [
                s for s in services if question.type not in catalog.get_service(s).allowed_question_types
            ]
            issues.append(
                ValidationIssue(
                    "error",
                    location,
                    f"Le type '{question.type}' n'est pas supporte par : {', '.join(incompatible)}. "
                    "Restreindre la question via 'only_services' ou changer de type.",
                )
            )
        for service_code in restricted_to:
            if not catalog.exists(service_code):
                issues.append(ValidationIssue("error", location, f"Service inconnu : {service_code}"))
            elif service_code not in services:
                issues.append(
                    ValidationIssue(
                        "warning",
                        location,
                        f"Question restreinte a {service_code}, qui n'est pas un mode de l'enquete.",
                    )
                )
        del applicable

        # Coherence type / parametres
        if q_type.is_choice and not question.choice_list_id:
            issues.append(ValidationIssue("error", location, "Question a modalites sans liste de choix."))
        if question.choice_list_id and question.choice_list and question.choice_list.code not in list_codes:
            issues.append(ValidationIssue("error", location, "Liste de choix introuvable."))
        if question.choice_list is not None and not question.choice_list.choices:
            issues.append(ValidationIssue("error", location, "La liste de choix est vide."))
        if q_type == QuestionType.CALCULATE and not question.calculation:
            issues.append(ValidationIssue("error", location, "Question calculee sans formule."))
        if (
            question.min_value is not None
            and question.max_value is not None
            and question.min_value > question.max_value
        ):
            issues.append(ValidationIssue("error", location, "Borne minimale superieure a la borne maximale."))
        if q_type == QuestionType.CALCULATE and question.is_required:
            issues.append(
                ValidationIssue("warning", location, "Une question calculee n'a pas besoin d'etre obligatoire.")
            )

        # Expressions : validite syntaxique et references
        known = set(codes[:-1])  # seules les questions deja posees sont connues
        for kind, expr in (
            ("relevance", question.relevance),
            ("constraint", question.constraint_expr),
            ("calculation", question.calculation),
        ):
            if not expr:
                continue
            for error in validate_expression(expr, known_variables=set(seen_codes)):
                issues.append(ValidationIssue("error", f"{location}.{kind}", error))
            forward_refs = referenced_variables(expr) - known - {question.code}
            forward_refs -= {"value"}
            forward_refs &= set(seen_codes) | (set(codes) - known)
            for ref in sorted(forward_refs):
                if ref in codes and codes.index(ref) > codes.index(question.code):
                    issues.append(
                        ValidationIssue(
                            "error",
                            f"{location}.{kind}",
                            f"Reference a '{ref}', qui est posee plus loin dans le questionnaire.",
                        )
                    )

        if question.constraint_expr and not question.constraint_message:
            issues.append(
                ValidationIssue(
                    "warning", location, "Contrainte sans message d'erreur destine a l'enqueteur."
                )
            )

    # Sections repetables : la question de comptage doit exister et etre entiere
    for section in questionnaire.sections:
        if not section.is_repeatable:
            continue
        loc = f"section:{section.code}"
        count_code = section.repeat_count_question
        if not count_code:
            issues.append(ValidationIssue("error", loc, "Section repetable sans question de comptage."))
        elif count_code not in seen_codes:
            issues.append(ValidationIssue("error", loc, f"Question de comptage introuvable : {count_code}"))
        else:
            counter = next(q for q in questions if q.code == count_code)
            if counter.type != QuestionType.INTEGER.value:
                issues.append(
                    ValidationIssue("error", loc, "La question de comptage doit etre de type entier.")
                )

    # Un questionnaire multimode long est un signal d'alerte methodologique.
    for service_code in services:
        service = catalog.get_service(service_code)
        if service.code.value == "SMS" and len(questions) > 10:
            issues.append(
                ValidationIssue(
                    "warning",
                    "questionnaire",
                    f"{len(questions)} questions pour un mode SMS : viser 10 questions au maximum.",
                )
            )

    return issues


def publish(session: Session, questionnaire: Questionnaire, user_id: str | None = None) -> dict:
    """Publie une version apres validation ; leve si des erreurs subsistent."""
    survey = questionnaire.survey
    issues = validate(questionnaire, survey)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise ValueError(
            "Publication impossible, "
            f"{len(errors)} erreur(s) : " + " | ".join(f"{e.location} {e.message}" for e in errors[:5])
        )

    schema = to_schema(questionnaire)
    questionnaire.status = QuestionnaireStatus.PUBLISHED.value
    questionnaire.published_at = utcnow()
    questionnaire.published_by = user_id
    questionnaire.schema_hash = schema_hash(schema)

    # Les versions anterieures publiees passent en archive.
    for other in survey.questionnaires:
        if other.id != questionnaire.id and other.status == QuestionnaireStatus.PUBLISHED.value:
            other.status = QuestionnaireStatus.ARCHIVED.value

    session.flush()
    return {
        "questionnaire_id": questionnaire.id,
        "version": questionnaire.version,
        "schema_hash": questionnaire.schema_hash,
        "warnings": [i.to_dict() for i in issues if i.severity == "warning"],
    }


def new_version(session: Session, questionnaire: Questionnaire) -> Questionnaire:
    """Cree un brouillon a partir d'une version existante."""
    survey = questionnaire.survey
    next_version = max((q.version for q in survey.questionnaires), default=0) + 1
    draft = Questionnaire(
        survey_id=survey.id,
        version=next_version,
        title=questionnaire.title,
        status=QuestionnaireStatus.DRAFT.value,
        settings=dict(questionnaire.settings or {}),
    )
    session.add(draft)
    session.flush()
    from_schema(session, draft, to_schema(questionnaire))
    return draft
