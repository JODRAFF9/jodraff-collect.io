"""Jeu de demonstration : une enquete multimode complete, du questionnaire au terrain.

Cree une enquete « Conditions de vie des menages » collectee en CAPI et CATI,
avec un questionnaire publie, un reseau d'enqueteurs, un echantillon affecte
et des entretiens realises. Sert de base au pipeline et aux rapports.

Usage : python -m scripts.seed_demo [--reset]
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apps.api.db import engine, init_db, session_scope  # noqa: E402
from apps.api.models import (  # noqa: E402
    Assignment,
    AssignmentStatus,
    Base,
    EnumeratorProfile,
    InterviewStatus,
    Organization,
    Questionnaire,
    Quota,
    SampleUnit,
    Survey,
    SurveyStatus,
    User,
    UserRole,
    utcnow,
)
from apps.api.security import hash_password  # noqa: E402
from apps.api.services import collect as collect_service  # noqa: E402
from apps.api.services import fieldwork as fieldwork_service  # noqa: E402
from apps.api.services import questionnaire as qs  # noqa: E402

RNG = random.Random(20260816)

REGIONS = [
    ("Dakar", ["Dakar-Plateau", "Pikine", "Guediawaye"]),
    ("Thies", ["Thies-Nord", "Mbour", "Tivaouane"]),
    ("Saint-Louis", ["Saint-Louis", "Dagana", "Podor"]),
]


QUESTIONNAIRE_SCHEMA = {
    "title": "Conditions de vie des menages 2026",
    "settings": {"allow_back_navigation": True, "audio_audit_rate": 0.1},
    "choice_lists": [
        {
            "code": "oui_non",
            "name": "Oui / Non",
            "choices": [
                {"code": "oui", "label": {"fr": "Oui"}, "order": 1, "score": 1},
                {"code": "non", "label": {"fr": "Non"}, "order": 2, "score": 0},
            ],
        },
        {
            "code": "sexe",
            "name": "Sexe",
            "choices": [
                {"code": "F", "label": {"fr": "Femme"}, "order": 1},
                {"code": "M", "label": {"fr": "Homme"}, "order": 2},
            ],
        },
        {
            "code": "instruction",
            "name": "Niveau d'instruction",
            "choices": [
                {"code": "aucun", "label": {"fr": "Aucun"}, "order": 1, "score": 0},
                {"code": "primaire", "label": {"fr": "Primaire"}, "order": 2, "score": 1},
                {"code": "secondaire", "label": {"fr": "Secondaire"}, "order": 3, "score": 2},
                {"code": "superieur", "label": {"fr": "Superieur"}, "order": 4, "score": 3},
            ],
        },
        {
            "code": "sources_revenu",
            "name": "Sources de revenu",
            "choices": [
                {"code": "salaire", "label": {"fr": "Salaire"}, "order": 1},
                {"code": "commerce", "label": {"fr": "Commerce"}, "order": 2},
                {"code": "agriculture", "label": {"fr": "Agriculture"}, "order": 3},
                {"code": "transferts", "label": {"fr": "Transferts recus"}, "order": 4},
                {"code": "aucune", "label": {"fr": "Aucune"}, "order": 5, "is_exclusive": True},
            ],
        },
        {
            "code": "satisfaction",
            "name": "Satisfaction",
            "choices": [
                {"code": "1", "label": {"fr": "Tres insatisfait"}, "order": 1, "score": 1},
                {"code": "2", "label": {"fr": "Insatisfait"}, "order": 2, "score": 2},
                {"code": "3", "label": {"fr": "Neutre"}, "order": 3, "score": 3},
                {"code": "4", "label": {"fr": "Satisfait"}, "order": 4, "score": 4},
                {"code": "5", "label": {"fr": "Tres satisfait"}, "order": 5, "score": 5},
            ],
        },
    ],
    "sections": [
        {
            "code": "identification",
            "label": {"fr": "Identification du menage"},
            "order": 1,
            "questions": [
                {
                    "code": "region",
                    "type": "single_choice",
                    "label": {"fr": "Region de residence"},
                    "order": 1,
                    "required": True,
                    "choice_list": "region_list",
                    "analysis_role": "dimension",
                },
                {
                    "code": "taille_menage",
                    "type": "integer",
                    "label": {"fr": "Nombre de personnes vivant dans le menage"},
                    "order": 2,
                    "required": True,
                    "min": 1,
                    "max": 30,
                    "constraint": "value >= 1",
                    "constraint_message": {"fr": "Le menage compte au moins une personne."},
                    "analysis_role": "mesure",
                },
                {
                    "code": "sexe_chef",
                    "type": "single_choice",
                    "label": {"fr": "Sexe du chef de menage"},
                    "order": 3,
                    "required": True,
                    "choice_list": "sexe",
                    "analysis_role": "dimension",
                },
                {
                    "code": "age_chef",
                    "type": "integer",
                    "label": {"fr": "Age du chef de menage"},
                    "order": 4,
                    "required": True,
                    "min": 15,
                    "max": 110,
                    "analysis_role": "mesure",
                },
            ],
        },
        {
            "code": "education",
            "label": {"fr": "Education"},
            "order": 2,
            "questions": [
                {
                    "code": "instruction_chef",
                    "type": "single_choice",
                    "label": {"fr": "Niveau d'instruction du chef de menage"},
                    "order": 1,
                    "required": True,
                    "choice_list": "instruction",
                    "analysis_role": "dimension",
                },
                {
                    "code": "enfants_scolarises",
                    "type": "single_choice",
                    "label": {"fr": "Tous les enfants en age scolaire sont-ils scolarises ?"},
                    "order": 2,
                    "relevance": "taille_menage > 1",
                    "choice_list": "oui_non",
                    "analysis_role": "dimension",
                },
            ],
        },
        {
            "code": "revenus",
            "label": {"fr": "Revenus et activite"},
            "order": 3,
            "questions": [
                {
                    "code": "sources",
                    "type": "multi_choice",
                    "label": {"fr": "Sources de revenu du menage"},
                    "order": 1,
                    "required": True,
                    "choice_list": "sources_revenu",
                    "analysis_role": "dimension",
                },
                {
                    "code": "revenu_mensuel",
                    "type": "decimal",
                    "label": {"fr": "Revenu mensuel total du menage (FCFA)"},
                    "order": 2,
                    "min": 0,
                    "max": 50000000,
                    "relevance": "not selected(sources, 'aucune')",
                    "analysis_role": "mesure",
                },
                {
                    "code": "revenu_par_tete",
                    "type": "calculate",
                    "label": {"fr": "Revenu par tete"},
                    "order": 3,
                    "calculation": "revenu_mensuel / max(taille_menage, 1)",
                    "analysis_role": "mesure",
                },
            ],
        },
        {
            "code": "appreciation",
            "label": {"fr": "Appreciation"},
            "order": 4,
            "questions": [
                {
                    "code": "satisfaction_vie",
                    "type": "scale",
                    "label": {"fr": "Satisfaction a l'egard des conditions de vie"},
                    "order": 1,
                    "required": True,
                    "choice_list": "satisfaction",
                    "min": 1,
                    "max": 5,
                    "analysis_role": "mesure",
                },
                {
                    "code": "photo_habitation",
                    "type": "photo",
                    "label": {"fr": "Photographie de l'habitation"},
                    "order": 2,
                    "only_services": ["CAPI"],
                },
                {
                    "code": "observations",
                    "type": "text",
                    "label": {"fr": "Observations de l'enqueteur"},
                    "order": 3,
                },
            ],
        },
    ],
}


def _region_choice_list() -> dict:
    return {
        "code": "region_list",
        "name": "Regions",
        "choices": [
            {"code": name, "label": {"fr": name}, "order": index + 1}
            for index, (name, _) in enumerate(REGIONS)
        ],
    }


def seed(reset: bool = False) -> dict:
    if reset:
        Base.metadata.drop_all(bind=engine)
    init_db()

    summary: dict = {}

    with session_scope() as session:
        if session.query(Organization).count() > 0 and not reset:
            return {"skipped": "Des donnees existent deja. Relancer avec --reset."}

        org = Organization(code="ANSD", name="Agence nationale de la statistique", country="SN")
        session.add(org)
        session.flush()

        admin = User(
            org_id=org.id,
            email="admin@jodraff.test",
            full_name="Administratrice plateforme",
            role=UserRole.ADMIN.value,
            password_hash=hash_password("motdepasse123"),
        )
        methodologist = User(
            org_id=org.id,
            email="conception@jodraff.test",
            full_name="Chargee de conception",
            role=UserRole.METHODOLOGIST.value,
            password_hash=hash_password("motdepasse123"),
        )
        supervisor = User(
            org_id=org.id,
            email="supervision@jodraff.test",
            full_name="Superviseur terrain",
            role=UserRole.SUPERVISOR.value,
            password_hash=hash_password("motdepasse123"),
        )
        analyst = User(
            org_id=org.id,
            email="analyse@jodraff.test",
            full_name="Analyste donnees",
            role=UserRole.ANALYST.value,
            password_hash=hash_password("motdepasse123"),
        )
        session.add_all([admin, methodologist, supervisor, analyst])
        session.flush()

        # --- Reseau d'enqueteurs ------------------------------------------
        enumerators = []
        for index in range(9):
            zone = REGIONS[index % len(REGIONS)][0]
            account = User(
                org_id=org.id,
                email=f"enqueteur{index + 1}@jodraff.test",
                full_name=f"Enqueteur {index + 1:02d}",
                phone=f"+2217700000{index + 1:02d}",
                role=UserRole.ENUMERATOR.value,
                password_hash=hash_password("motdepasse123"),
            )
            session.add(account)
            session.flush()
            session.add(
                EnumeratorProfile(
                    user_id=account.id,
                    supervisor_id=supervisor.id,
                    matricule=f"ENQ{index + 1:03d}",
                    base_zone=zone,
                    device_id=f"TAB-{index + 1:03d}",
                    hired_at=date.today() - timedelta(days=60),
                    daily_capacity=RNG.choice([6, 7, 8, 9]),
                    certified_services=["CAPI", "CATI"] if index % 3 else ["CAPI"],
                    training_completed=True,
                    status="active",
                )
            )
            enumerators.append(account)

        # --- Enquete -------------------------------------------------------
        survey = Survey(
            org_id=org.id,
            code="ECVM2026",
            title="Enquete sur les conditions de vie des menages 2026",
            description=(
                "Enquete nationale multimode : passage en face a face dans les zones "
                "rurales, relance telephonique en milieu urbain."
            ),
            status=SurveyStatus.DESIGN.value,
            collection_services=["CAPI", "CATI"],
            target_sample=600,
            start_date=date.today() - timedelta(days=21),
            end_date=date.today() + timedelta(days=30),
            default_language="fr",
            languages=["fr"],
            created_by=methodologist.id,
        )
        session.add(survey)
        session.flush()

        questionnaire = Questionnaire(
            survey_id=survey.id, version=1, title=QUESTIONNAIRE_SCHEMA["title"]
        )
        session.add(questionnaire)
        session.flush()

        schema = dict(QUESTIONNAIRE_SCHEMA)
        schema["choice_lists"] = [_region_choice_list(), *QUESTIONNAIRE_SCHEMA["choice_lists"]]
        qs.from_schema(session, questionnaire, schema)

        issues = qs.validate(questionnaire, survey)
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            raise SystemExit("Le questionnaire de demonstration est invalide : " + str(errors))

        qs.publish(session, questionnaire, user_id=methodologist.id)
        survey.status = SurveyStatus.ACTIVE.value
        session.flush()

        # --- Quotas --------------------------------------------------------
        for region, _ in REGIONS:
            session.add(
                Quota(
                    survey_id=survey.id,
                    label=f"{region} — chef de menage femme",
                    dimensions={"region": region, "sexe_chef": "F"},
                    target=60,
                )
            )
        session.flush()

        # --- Echantillon ---------------------------------------------------
        units = []
        for index in range(600):
            region, communes = REGIONS[index % len(REGIONS)]
            unit = SampleUnit(
                survey_id=survey.id,
                code=f"MEN{index + 1:05d}",
                contact_name=f"Menage {index + 1}",
                phone=f"+2217{RNG.randint(10000000, 99999999)}",
                geo_level_1=region,
                geo_level_2=RNG.choice(communes),
                geo_level_3=f"Quartier {RNG.randint(1, 12)}",
                stratum="urbain" if index % 3 else "rural",
                sampling_weight=round(RNG.uniform(0.8, 1.4), 3),
            )
            session.add(unit)
            units.append(unit)
        session.flush()

        for index, unit in enumerate(units):
            session.add(
                Assignment(
                    survey_id=survey.id,
                    sample_unit_id=unit.id,
                    collection_service="CAPI" if index % 3 else "CATI",
                    status=AssignmentStatus.PENDING.value,
                    priority=RNG.choice([2, 3, 3, 4]),
                )
            )
        session.flush()

        assign_capi = fieldwork_service.auto_assign(session, survey, "CAPI", due_in_days=21)
        assign_cati = fieldwork_service.auto_assign(session, survey, "CATI", due_in_days=21)

        # --- Entretiens ----------------------------------------------------
        completed = 0
        assignments = (
            session.query(Assignment)
            .filter(
                Assignment.survey_id == survey.id,
                Assignment.status == AssignmentStatus.ASSIGNED.value,
            )
            .limit(380)
            .all()
        )

        for index, assignment in enumerate(assignments):
            unit = session.get(SampleUnit, assignment.sample_unit_id)
            interview = collect_service.start_interview(
                session,
                survey,
                assignment.collection_service,
                enumerator_id=assignment.enumerator_id,
                assignment_id=assignment.id,
                client_uuid=f"demo-{index:05d}",
                device_id=f"TAB-{(index % 9) + 1:03d}",
                latitude=round(14.6 + RNG.uniform(-1.5, 1.5), 5),
                longitude=round(-17.4 + RNG.uniform(-1.5, 1.5), 5),
            )
            # Etalement des entretiens sur les trois dernieres semaines.
            interview.started_at = utcnow() - timedelta(
                days=RNG.randint(0, 20), hours=RNG.randint(0, 9)
            )

            taille = RNG.randint(1, 14)
            answers = {
                "region": unit.geo_level_1,
                "taille_menage": taille,
                "sexe_chef": RNG.choices(["F", "M"], weights=[0.34, 0.66])[0],
                "age_chef": RNG.randint(22, 88),
                "instruction_chef": RNG.choices(
                    ["aucun", "primaire", "secondaire", "superieur"],
                    weights=[0.30, 0.32, 0.28, 0.10],
                )[0],
                "satisfaction_vie": RNG.choices([1, 2, 3, 4, 5], weights=[0.12, 0.2, 0.31, 0.26, 0.11])[0],
            }
            if taille > 1:
                answers["enfants_scolarises"] = RNG.choices(["oui", "non"], weights=[0.72, 0.28])[0]

            sources = RNG.choice(
                [["salaire"], ["commerce"], ["agriculture"], ["salaire", "commerce"],
                 ["commerce", "transferts"], ["aucune"]]
            )
            answers["sources"] = sources
            if "aucune" not in sources:
                answers["revenu_mensuel"] = float(RNG.randint(45_000, 850_000))

            if assignment.collection_service == "CAPI" and RNG.random() < 0.6:
                answers["photo_habitation"] = {"uri": f"blob://photos/{interview.id}.jpg"}
            if RNG.random() < 0.25:
                answers["observations"] = RNG.choice(
                    [
                        "Menage cooperatif.",
                        "Entretien interrompu puis repris.",
                        "Adresse difficile a localiser.",
                    ]
                )

            for code, value in answers.items():
                try:
                    collect_service.save_answer(session, interview, code, value)
                except collect_service.CollectError:
                    continue

            # Duree realiste, avec quelques entretiens anormalement rapides pour
            # que le controle qualite ait matiere a signaler.
            base_minutes = 42 if assignment.collection_service == "CAPI" else 19
            factor = 0.15 if RNG.random() < 0.05 else RNG.uniform(0.7, 1.5)
            planned_end = interview.started_at + timedelta(seconds=int(base_minutes * 60 * factor))

            try:
                collect_service.complete_interview(session, interview, allow_partial=True)
            except collect_service.CollectError:
                continue

            # La cloture horodate l'entretien a l'instant present : on replace
            # la fin sur la date simulee avant de recalculer la qualite, qui
            # depend justement de la duree.
            interview.ended_at = planned_end
            interview.submitted_at = planned_end
            interview.duration_seconds = int((planned_end - interview.started_at).total_seconds())
            score, flags = collect_service.compute_quality(session, interview)
            interview.quality_score, interview.quality_flags = score, flags

            if interview.status == InterviewStatus.COMPLETED.value:
                if score >= 0.6:
                    collect_service.validate_interview(
                        session, interview, supervisor.id, accepted=True
                    )
                elif RNG.random() < 0.4:
                    collect_service.validate_interview(
                        session, interview, supervisor.id, accepted=False,
                        notes="Duree anormale, a repasser.",
                    )
            completed += 1

        session.flush()

        summary = {
            "organisation": org.name,
            "survey_id": survey.id,
            "survey_code": survey.code,
            "questionnaire_version": questionnaire.version,
            "schema_hash": questionnaire.schema_hash,
            "enumerators": len(enumerators),
            "sample_units": len(units),
            "assignments_capi": assign_capi["assigned"],
            "assignments_cati": assign_cati["assigned"],
            "interviews": completed,
            "comptes": {
                "admin": "admin@jodraff.test",
                "conception": "conception@jodraff.test",
                "supervision": "supervision@jodraff.test",
                "analyse": "analyse@jodraff.test",
                "mot_de_passe": "motdepasse123",
            },
        }

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Jeu de demonstration Jodraff Collect")
    parser.add_argument("--reset", action="store_true", help="Vider la base avant de recreer")
    args = parser.parse_args()

    import json

    print(json.dumps(seed(reset=args.reset), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
