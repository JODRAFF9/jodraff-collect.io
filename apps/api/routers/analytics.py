"""Traitement et restitution : declenchement du pipeline et lecture de la couche gold.

Ces routes exposent l'entrepot analytique a l'application et aux outils de
restitution. Power BI se connecte de preference directement au Parquet gold
ou a Fabric ; ces points d'entree servent au pilotage en temps reel dans
l'interface et a la generation des rapports.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from apps.api.deps import current_user, require_roles
from apps.api.models import User, UserRole
from data_platform import quality as quality_module
from data_platform.runner import connect_warehouse, run_pipeline, write_report

router = APIRouter(prefix="/api/v1/analytics", tags=["Traitement et restitution"])

_ANALYSTS = require_roles(UserRole.ANALYST, UserRole.SUPERVISOR, UserRole.METHODOLOGIST)

# Seules ces vues sont interrogeables : evite toute lecture arbitraire de l'entrepot.
_ALLOWED_MARTS = {
    "mart_frequency_table",
    "mart_numeric_summary",
    "mart_collection_progress",
    "mart_enumerator_scorecard",
    "mart_mode_effect",
    "mart_quality_overview",
}


def _query(sql: str, params: list | None = None) -> list[dict]:
    con = connect_warehouse(read_only=True)
    try:
        cursor = con.execute(sql, params or [])
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        con.close()


@router.post("/pipeline/run", summary="Executer le pipeline medallion")
def trigger_pipeline(
    full_refresh: bool = False, user: User = Depends(_ANALYSTS)
) -> dict:
    """Lance bronze -> silver -> gold, puis les controles qualite.

    En production, cette execution est planifiee par Azure Data Factory ou
    Fabric Data Pipeline ; ce point d'entree sert aux rafraichissements a la
    demande depuis l'interface.
    """
    report = run_pipeline(full_refresh=full_refresh)
    path = write_report(report)
    payload = report.to_dict()
    payload["report_path"] = str(path)
    return payload


@router.get("/quality", summary="Resultat des controles qualite de l'entrepot")
def quality_report(user: User = Depends(_ANALYSTS)) -> dict:
    con = connect_warehouse(read_only=True)
    try:
        results = quality_module.run_checks(con)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Entrepot indisponible : {exc}. Executer le pipeline au prealable.",
        ) from exc
    finally:
        con.close()
    return {"summary": quality_module.summarise(results), "checks": results}


@router.get("/marts/{mart_name}", summary="Lire une vue de restitution")
def read_mart(
    mart_name: str,
    survey_key: str | None = None,
    question_code: str | None = None,
    limit: int = Query(default=500, ge=1, le=10_000),
    user: User = Depends(current_user),
) -> dict:
    """Lecture filtree d'une vue gold, en parametres lies."""
    if mart_name not in _ALLOWED_MARTS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vue inconnue. Disponibles : {', '.join(sorted(_ALLOWED_MARTS))}",
        )

    clauses, params = [], []
    if survey_key:
        clauses.append("survey_key = ?")
        params.append(survey_key)
    if question_code:
        clauses.append("question_code = ?")
        params.append(question_code)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM {mart_name}{where} LIMIT {int(limit)}"  # noqa: S608 - nom en liste blanche

    try:
        rows = _query(sql, params)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Lecture impossible : {exc}. Executer le pipeline au prealable.",
        ) from exc

    return {"mart": mart_name, "count": len(rows), "rows": rows}


@router.get("/surveys/{survey_key}/summary", summary="Synthese analytique d'une enquete")
def survey_summary(survey_key: str, user: User = Depends(current_user)) -> dict:
    """Agrege ce qu'il faut pour une page de restitution ou un rapport."""
    try:
        progress = _query(
            "SELECT * FROM mart_collection_progress WHERE survey_key = ?", [survey_key]
        )
        frequencies = _query(
            "SELECT * FROM mart_frequency_table WHERE survey_key = ? "
            "ORDER BY question_code, n_weighted DESC LIMIT 500",
            [survey_key],
        )
        numeric = _query(
            "SELECT * FROM mart_numeric_summary WHERE survey_key = ?", [survey_key]
        )
        quality = _query(
            "SELECT * FROM mart_quality_overview WHERE survey_key = ? "
            "ORDER BY avg_quality_score LIMIT 100",
            [survey_key],
        )
        mode_effect = _query(
            "SELECT * FROM mart_mode_effect WHERE survey_key = ? "
            "ORDER BY spread_points DESC LIMIT 50",
            [survey_key],
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Entrepot indisponible : {exc}. Executer le pipeline au prealable.",
        ) from exc

    return {
        "survey_key": survey_key,
        "progress": progress,
        "frequency_tables": frequencies,
        "numeric_summaries": numeric,
        "quality": quality,
        "mode_effect": mode_effect,
    }


@router.get("/powerbi/connection", summary="Parametres de connexion Power BI")
def powerbi_connection(user: User = Depends(_ANALYSTS)) -> dict:
    """Indique a l'analyste comment brancher Power BI sur la couche gold."""
    from platform_core.config import settings

    return {
        "mode": "local" if settings.environment == "local" else "cloud",
        "gold_path": str(settings.layer_path("gold")),
        "tables": [
            "dim_date",
            "dim_survey",
            "dim_collection_service",
            "dim_enumerator",
            "dim_question",
            "dim_geography",
            "fact_interview",
            "fact_answer",
            "fact_fieldwork_daily",
            "fact_quota",
        ],
        "recommended_storage_mode": "Import pour les dimensions, DirectQuery pour fact_answer",
        "documentation": "powerplatform/powerbi/README.md",
    }
