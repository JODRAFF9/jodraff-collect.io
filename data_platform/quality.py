"""Controles qualite du pipeline.

Les tests s'executent sur l'entrepot apres transformation. Un test en erreur
signale des donnees qui ne doivent pas etre publiees ; un avertissement
signale une anomalie a surveiller sans bloquer la restitution.

La logique suit les familles classiques : unicite, non-nullite, integrite
referentielle, plages de valeurs et coherence metier.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb


@dataclass(frozen=True)
class Check:
    name: str
    sql: str  # doit retourner une seule colonne : le nombre de lignes en anomalie
    severity: str = "error"  # error | warning
    description: str = ""


CHECKS: tuple[Check, ...] = (
    # --- Unicite ----------------------------------------------------------
    Check(
        name="unique_interview_key",
        sql="SELECT COUNT(*) FROM (SELECT interview_key FROM fact_interview "
        "GROUP BY interview_key HAVING COUNT(*) > 1)",
        description="La cle d'entretien doit etre unique dans la table de faits.",
    ),
    Check(
        name="unique_answer_key",
        sql="SELECT COUNT(*) FROM (SELECT answer_key FROM fact_answer "
        "GROUP BY answer_key HAVING COUNT(*) > 1)",
        description="Une reponse ne doit apparaitre qu'une fois.",
    ),
    Check(
        name="unique_answer_slot",
        sql="SELECT COUNT(*) FROM (SELECT interview_key, question_code, repeat_index "
        "FROM fact_answer GROUP BY 1, 2, 3 HAVING COUNT(*) > 1)",
        description="Une seule reponse par entretien, question et rang de repetition.",
    ),
    Check(
        name="unique_date_key",
        sql="SELECT COUNT(*) FROM (SELECT date_key FROM dim_date "
        "GROUP BY date_key HAVING COUNT(*) > 1)",
        description="La dimension date ne doit pas comporter de doublon.",
    ),
    # --- Non-nullite ------------------------------------------------------
    Check(
        name="interview_has_survey",
        sql="SELECT COUNT(*) FROM fact_interview WHERE survey_key IS NULL",
        description="Tout entretien est rattache a une enquete.",
    ),
    Check(
        name="answer_has_interview",
        sql="SELECT COUNT(*) FROM fact_answer WHERE interview_key IS NULL",
        description="Toute reponse appartient a un entretien.",
    ),
    # --- Integrite referentielle -----------------------------------------
    Check(
        name="fk_interview_survey",
        sql="SELECT COUNT(*) FROM fact_interview f "
        "LEFT JOIN dim_survey d ON d.survey_key = f.survey_key WHERE d.survey_key IS NULL",
        description="Chaque entretien pointe vers une enquete existante.",
    ),
    Check(
        name="fk_answer_interview",
        sql="SELECT COUNT(*) FROM fact_answer f "
        "LEFT JOIN fact_interview i ON i.interview_key = f.interview_key "
        "WHERE i.interview_key IS NULL",
        description="Aucune reponse orpheline.",
    ),
    Check(
        name="fk_interview_date",
        sql="SELECT COUNT(*) FROM fact_interview f "
        "LEFT JOIN dim_date d ON d.date_key = f.date_key "
        "WHERE f.date_key IS NOT NULL AND d.date_key IS NULL",
        description="La dimension date couvre toute la periode de collecte.",
    ),
    Check(
        name="fk_interview_enumerator",
        sql="SELECT COUNT(*) FROM fact_interview f "
        "LEFT JOIN dim_enumerator e ON e.enumerator_key = f.enumerator_key "
        "WHERE f.enumerator_key IS NOT NULL AND e.enumerator_key IS NULL",
        severity="warning",
        description="Un entretien reference un enqueteur absent du referentiel.",
    ),
    Check(
        name="fk_answer_question",
        sql="SELECT COUNT(*) FROM fact_answer f "
        "LEFT JOIN dim_question q ON q.question_key = f.question_key "
        "WHERE f.question_key IS NOT NULL AND q.question_key IS NULL",
        severity="warning",
        description="Une reponse reference une question absente du referentiel.",
    ),
    # --- Plages de valeurs -----------------------------------------------
    Check(
        name="quality_score_range",
        sql="SELECT COUNT(*) FROM fact_interview "
        "WHERE quality_score IS NOT NULL AND (quality_score < 0 OR quality_score > 1)",
        description="Le score de qualite est borne entre 0 et 1.",
    ),
    Check(
        name="duration_not_negative",
        sql="SELECT COUNT(*) FROM fact_interview WHERE duration_seconds < 0",
        description="Une duree d'entretien ne peut pas etre negative.",
    ),
    Check(
        name="sampling_weight_positive",
        sql="SELECT COUNT(*) FROM fact_interview WHERE sampling_weight <= 0",
        description="Le poids de sondage doit etre strictement positif.",
    ),
    Check(
        name="known_collection_service",
        sql="SELECT COUNT(*) FROM fact_interview f "
        "LEFT JOIN dim_collection_service c ON c.collection_service = f.collection_service "
        "WHERE c.collection_service IS NULL",
        description="Le mode de collecte appartient au catalogue.",
    ),
    # --- Coherence metier -------------------------------------------------
    Check(
        name="usable_interviews_have_answers",
        sql="SELECT COUNT(*) FROM fact_interview i "
        "LEFT JOIN (SELECT interview_key, COUNT(*) n FROM fact_answer GROUP BY 1) a "
        "  ON a.interview_key = i.interview_key "
        "WHERE i.usable_count = 1 AND COALESCE(a.n, 0) = 0",
        severity="warning",
        description="Un entretien exploitable sans aucune reponse est suspect.",
    ),
    Check(
        name="quota_not_overshot",
        sql="SELECT COUNT(*) FROM fact_quota "
        "WHERE is_blocking AND quota_achieved > quota_target * 1.1",
        severity="warning",
        description="Un quota bloquant depasse de plus de 10 % sa cible.",
    ),
    Check(
        name="no_future_interviews",
        sql="SELECT COUNT(*) FROM fact_interview WHERE interview_date > CURRENT_DATE",
        severity="warning",
        description="Une date d'entretien dans le futur revele une horloge d'appareil desynchronisee.",
    ),
    Check(
        name="pii_not_leaked_to_gold",
        sql="SELECT COUNT(*) FROM fact_answer f "
        "JOIN dim_question q ON q.question_key = f.question_key "
        "WHERE q.is_pii AND f.value_text IS NOT NULL",
        description="Aucune donnee personnelle en clair ne doit atteindre la couche gold.",
    ),
)


def run_checks(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Execute tous les controles et retourne leur resultat."""
    results = []
    for check in CHECKS:
        try:
            failing = con.execute(check.sql).fetchone()[0]
            results.append(
                {
                    "name": check.name,
                    "severity": check.severity,
                    "passed": failing == 0,
                    "failing_rows": int(failing),
                    "description": check.description,
                }
            )
        except Exception as exc:  # noqa: BLE001 - un test qui plante est un echec
            results.append(
                {
                    "name": check.name,
                    "severity": check.severity,
                    "passed": False,
                    "failing_rows": -1,
                    "description": check.description,
                    "error": str(exc),
                }
            )
    return results


def summarise(results: list[dict]) -> dict:
    """Synthese lisible d'une execution de controles."""
    errors = [r for r in results if not r["passed"] and r["severity"] == "error"]
    warnings = [r for r in results if not r["passed"] and r["severity"] == "warning"]
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "errors": len(errors),
        "warnings": len(warnings),
        "failed_checks": [r["name"] for r in errors + warnings],
        "publishable": not errors,
    }
