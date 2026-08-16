"""Production des rapports LaTeX a partir de la couche gold.

Le generateur lit exclusivement l'entrepot analytique : aucune requete a la
base operationnelle, aucun recalcul statistique en Python. Les chiffres du
rapport sont donc, par construction, ceux des tableaux de bord Power BI, qui
lisent les memes tables gold.

Usage :
    python -m reports.generate --survey-code ECVM2026
    python -m reports.generate --survey-code ECVM2026 --no-pdf   # .tex seulement
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from data_platform import quality as quality_module
from data_platform.runner import connect_warehouse
from platform_core.config import settings

TEMPLATE_DIR = Path(__file__).resolve().parent / "latex"

# Caracteres devant etre echappes pour ne pas etre interpretes par LaTeX.
_TEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape_tex(value) -> str:
    """Echappe une valeur destinee au corps du document LaTeX.

    Deux problemes distincts sont traites ici. D'abord l'echappement : un
    libelle de modalite contenant « & » ou « % », cas courant dans un
    questionnaire, casse la compilation. Ensuite les ligatures de tirets :
    LaTeX transforme deux traits d'union consecutifs en tiret demi-cadratin et
    trois en tiret cadratin. Un libelle saisi « Oui -- Non » ne serait donc pas
    restitue tel quel. On insere un groupe vide entre les traits pour rompre la
    ligature, sans rien changer au texte affiche.
    """
    if value is None:
        return ""
    text = "".join(_TEX_ESCAPES.get(char, char) for char in str(value))
    return re.sub(r"-{2,}", lambda m: "-" + "{}-" * (len(m.group()) - 1), text)


# Marque de valeur absente dans les tableaux. On evite la suite « -- », que
# LaTeX transforme en tiret demi-cadratin : dans une colonne de chiffres, ce
# trait se confond avec un signe negatif.
VALEUR_ABSENTE = "n.d."


def fr_number(value, decimals: int = 1) -> str:
    """Formate un nombre a la francaise (virgule decimale)."""
    if value is None:
        return VALEUR_ABSENTE
    return f"{float(value):.{decimals}f}".replace(".", ",")


def build_environment() -> Environment:
    """Environnement Jinja aux delimiteurs compatibles avec LaTeX."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        block_start_string=r"\BLOCK{",
        block_end_string="}",
        variable_start_string=r"\VAR{",
        variable_end_string="}",
        comment_start_string="%#",
        comment_end_string="#%",
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
        undefined=StrictUndefined,
    )
    env.filters["tex"] = escape_tex
    env.filters["fr"] = fr_number
    return env


# --------------------------------------------------------------------------
# Extraction depuis la couche gold
# --------------------------------------------------------------------------


class WarehouseEmpty(RuntimeError):
    """L'entrepot ne contient pas l'enquete demandee."""


def _rows(con, sql: str, params: list | None = None) -> list[dict]:
    cursor = con.execute(sql, params or [])
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def collect_context(survey_code: str, max_tables: int = 12) -> dict:
    """Rassemble toutes les donnees necessaires au rapport."""
    con = connect_warehouse(read_only=True)
    try:
        surveys = _rows(con, "SELECT * FROM dim_survey WHERE survey_code = ?", [survey_code])
        if not surveys:
            raise WarehouseEmpty(
                f"L'enquete {survey_code} est absente de l'entrepot. "
                "Executer le pipeline avant de generer le rapport."
            )
        survey = surveys[0]
        key = survey["survey_key"]

        avancement = _rows(
            con,
            "SELECT * FROM mart_collection_progress WHERE survey_key = ? "
            "ORDER BY interviews_total DESC",
            [key],
        )
        stats = _rows(
            con,
            "SELECT question_code, question_label, SUM(n) AS n, "
            "       AVG(mean) AS mean, AVG(weighted_mean) AS weighted_mean, "
            "       AVG(median) AS median, AVG(ci95_half_width) AS ci95 "
            "FROM mart_numeric_summary WHERE survey_key = ? "
            "GROUP BY question_code, question_label ORDER BY question_code",
            [key],
        )
        frequences = _rows(
            con,
            "SELECT question_code, question_label, modality, "
            "       SUM(n_raw) AS n_raw, SUM(n_weighted) AS n_weighted "
            "FROM mart_frequency_table WHERE survey_key = ? "
            "GROUP BY question_code, question_label, modality "
            "ORDER BY question_code, n_raw DESC",
            [key],
        )
        rendement = _rows(
            con,
            "SELECT * FROM mart_enumerator_scorecard "
            "WHERE enumerator_key IN (SELECT DISTINCT enumerator_key FROM fact_interview "
            "                          WHERE survey_key = ?) "
            "ORDER BY interviews_usable DESC LIMIT 60",
            [key],
        )
        qualite = _rows(
            con,
            "SELECT * FROM mart_quality_overview WHERE survey_key = ? "
            "ORDER BY avg_quality_score LIMIT 10",
            [key],
        )
        effet_mode = _rows(
            con,
            "SELECT * FROM mart_mode_effect WHERE survey_key = ? "
            "ORDER BY spread_points DESC LIMIT 12",
            [key],
        )
        dictionnaire = _rows(
            con,
            "SELECT question_code, question_label, question_type, analysis_role "
            "FROM dim_question WHERE survey_key = ? ORDER BY absolute_order",
            [key],
        )
        # Version d'instrument effectivement utilisee et son empreinte : c'est
        # ce qui rend le rapport rattachable a un questionnaire precis.
        instrument = _rows(
            con,
            "SELECT questionnaire_version, schema_hash FROM dim_question "
            "WHERE survey_key = ? ORDER BY questionnaire_version DESC LIMIT 1",
            [key],
        )
        enqueteurs = con.execute(
            "SELECT COUNT(DISTINCT enumerator_key) FROM fact_interview "
            "WHERE survey_key = ? AND enumerator_key IS NOT NULL",
            [key],
        ).fetchone()[0]
        courbe = _rows(
            con,
            "SELECT interview_date, SUM(usable_count) AS jour "
            "FROM fact_interview WHERE survey_key = ? AND interview_date IS NOT NULL "
            "GROUP BY interview_date ORDER BY interview_date",
            [key],
        )
        checks = quality_module.run_checks(con)
    finally:
        con.close()

    return _shape_context(
        survey,
        avancement,
        stats,
        frequences,
        rendement,
        qualite,
        effet_mode,
        dictionnaire,
        courbe,
        checks,
        instrument[0] if instrument else {},
        enqueteurs,
        max_tables,
    )


def _shape_context(
    survey, avancement, stats, frequences, rendement, qualite, effet_mode,
    dictionnaire, courbe, checks, instrument, enqueteurs, max_tables,
) -> dict:
    """Met les donnees brutes au format attendu par le gabarit."""
    total = sum(row["interviews_total"] or 0 for row in avancement)
    exploitables = sum(row["interviews_usable"] or 0 for row in avancement)
    valides = sum(row["interviews_validated"] or 0 for row in avancement)
    rejetes = sum(row["interviews_rejected"] or 0 for row in avancement)
    cible = survey["target_sample"] or 0

    duree_moyenne = (
        sum((row["avg_duration_minutes"] or 0) * (row["interviews_total"] or 0) for row in avancement)
        / total
        if total
        else 0
    )
    score_moyen = (
        sum((row["avg_quality_score"] or 0) * (row["interviews_total"] or 0) for row in avancement)
        / total
        if total
        else 0
    )

    # Tris a plat : un tableau par question qualitative, plafonne pour ne pas
    # produire un rapport de cent pages sur un questionnaire long.
    par_question: dict[str, dict] = {}
    for row in frequences:
        entry = par_question.setdefault(
            row["question_code"],
            {"libelle": row["question_label"] or row["question_code"], "lignes": [], "total": 0},
        )
        entry["lignes"].append(row)
        entry["total"] += int(row["n_raw"] or 0)

    tris_a_plat = []
    for entry in list(par_question.values())[:max_tables]:
        total_brut = entry["total"] or 1
        total_pondere = sum(float(r["n_weighted"] or 0) for r in entry["lignes"]) or 1.0
        tris_a_plat.append(
            {
                "libelle": entry["libelle"],
                "total": entry["total"],
                "lignes": [
                    {
                        "modalite": r["modality"],
                        "effectif": int(r["n_raw"] or 0),
                        "pct_brut": fr_number(100 * (r["n_raw"] or 0) / total_brut),
                        "pct_pondere": fr_number(100 * float(r["n_weighted"] or 0) / total_pondere),
                    }
                    for r in entry["lignes"]
                ],
            }
        )

    # Courbe cumulee du rythme de collecte.
    courbe_collecte = []
    cumul = 0
    for index, row in enumerate(courbe, start=1):
        cumul += int(row["jour"] or 0)
        courbe_collecte.append({"jour": index, "cumul": cumul})

    ecarts = [float(r["spread_points"] or 0) for r in effet_mode]

    return {
        "enquete": {
            "titre": survey["survey_title"],
            "code": survey["survey_code"],
            "organisation": "Jodraff Collect",
            "modes": survey["collection_services_label"] or VALEUR_ABSENTE,
            "cible": cible,
            "version_questionnaire": instrument.get("questionnaire_version") or 1,
            "empreinte_schema": instrument.get("schema_hash") or "non publiee",
            "periode": _periode(survey),
        },
        "synthese": {
            "total": total,
            "exploitables": exploitables,
            "valides": valides,
            # Deux formes : une valeur numerique pour les conditions du gabarit,
            # une chaine formatee a la francaise pour l'affichage.
            "taux_realisation": round(100 * exploitables / cible, 1) if cible else 0.0,
            "taux_realisation_pct": fr_number(100 * exploitables / cible) if cible else "0,0",
            "taux_rejet_pct": fr_number(100 * rejetes / total) if total else "0,0",
            "duree_moyenne_min": fr_number(duree_moyenne),
            "score_qualite": fr_number(score_moyen, 3),
            "enqueteurs": enqueteurs,
            "modes_utilises": len(avancement),
        },
        "avancement": [
            {
                "service": row["service_name"] or row["collection_service"],
                "total": row["interviews_total"] or 0,
                "exploitables": row["interviews_usable"] or 0,
                "valides": row["interviews_validated"] or 0,
                "duree_min": fr_number(row["avg_duration_minutes"]),
                "qualite": fr_number(row["avg_quality_score"], 3),
            }
            for row in avancement
        ],
        "modes_detail": _modes_detail(avancement),
        "courbe_collecte": courbe_collecte,
        "tris_a_plat": tris_a_plat,
        "stats_numeriques": [
            {
                "libelle": row["question_label"] or row["question_code"],
                "n": int(row["n"] or 0),
                "moyenne": fr_number(row["mean"], 2),
                "moyenne_ponderee": fr_number(row["weighted_mean"], 2),
                "mediane": fr_number(row["median"], 2),
                "ic95": fr_number(row["ci95"], 2),
            }
            for row in stats
        ],
        "rendement": [
            {
                "matricule": row["matricule"] or VALEUR_ABSENTE,
                "service": row["collection_service"],
                "jours": row["days_worked"] or 0,
                "exploitables": row["interviews_usable"] or 0,
                "par_jour": fr_number(row["interviews_per_day"], 2),
                "taux_succes": fr_number(100 * (row["success_rate"] or 0)),
            }
            for row in rendement
        ],
        "qualite_alertes": [
            {
                "matricule": row["matricule"] or VALEUR_ABSENTE,
                "service": row["collection_service"],
                "entretiens": row["interviews"] or 0,
                "score": fr_number(row["avg_quality_score"], 3),
                "signales": row["interviews_flagged"] or 0,
            }
            for row in qualite
        ],
        "effet_mode": [
            {
                "libelle": row["question_label"] or row["question_code"],
                "modalite": row["modality"],
                "ecart": fr_number(row["spread_points"]),
                "modes": row["modes_observed"] or 0,
            }
            for row in effet_mode
        ],
        "effet_mode_max": round(max(ecarts), 1) if ecarts else 0,
        "dictionnaire": [
            {
                "code": row["question_code"],
                "libelle": row["question_label"] or "",
                "type": row["question_type"],
                "role": row["analysis_role"] or VALEUR_ABSENTE,
            }
            for row in dictionnaire
        ],
        "meta": {
            "date_generation": datetime.now(UTC).strftime("%d/%m/%Y a %H:%M UTC"),
            "controles_total": len(checks),
            "controles_reussis": sum(1 for c in checks if c["passed"]),
        },
    }


def _periode(survey) -> str:
    debut, fin = survey.get("start_date"), survey.get("end_date")
    if debut and fin:
        return f"du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}"
    return "periode non renseignee"


def _modes_detail(avancement) -> list[dict]:
    """Descriptif methodologique de chaque mode effectivement utilise."""
    from apps.api.services import collection_catalog as catalog

    detail = []
    for row in avancement:
        code = row["collection_service"]
        if not catalog.exists(code):
            continue
        service = catalog.get_service(code)
        detail.append(
            {
                "nom": service.name,
                "description": service.description,
                "duree": service.typical_duration_minutes,
                "taux_reponse_pct": int(service.typical_response_rate * 100),
            }
        )
    return detail


# --------------------------------------------------------------------------
# Rendu et compilation
# --------------------------------------------------------------------------


def render_tex(context: dict, template_name: str = "rapport_enquete.tex.j2") -> str:
    return build_environment().get_template(template_name).render(**context)


def compile_pdf(tex_path: Path, engine: str | None = None) -> Path | None:
    """Compile le .tex en PDF si un moteur LaTeX est disponible.

    Deux passes sont necessaires pour resoudre la table des matieres et les
    references de tableaux.
    """
    engine = engine or settings.latex_engine
    if shutil.which(engine) is None:
        return None

    workdir = tex_path.parent
    for _ in range(2):
        result = subprocess.run(  # noqa: S603 - moteur issu de la configuration
            [engine, "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            log = _extract_latex_error(result.stdout)
            raise RuntimeError(f"Echec de compilation LaTeX :\n{log}")

    pdf = tex_path.with_suffix(".pdf")
    return pdf if pdf.exists() else None


def _extract_latex_error(output: str) -> str:
    """Isole les lignes utiles d'un journal LaTeX, souvent tres verbeux."""
    lines = [line for line in output.splitlines() if line.startswith("!") or "Error" in line]
    return "\n".join(lines[-15:]) or output[-1500:]


def generate(survey_code: str, output_dir: Path | None = None, build_pdf: bool = True) -> dict:
    """Genere le rapport complet d'une enquete."""
    context = collect_context(survey_code)
    tex_source = render_tex(context)

    output_dir = output_dir or Path(settings.reports_output_dir) / survey_code
    output_dir.mkdir(parents=True, exist_ok=True)

    # Le preambule doit accompagner le .tex pour que \input le trouve.
    shutil.copy(TEMPLATE_DIR / "preambule.tex", output_dir / "preambule.tex")

    tex_path = output_dir / f"rapport_{_slug(survey_code)}.tex"
    tex_path.write_text(tex_source, encoding="utf-8")

    pdf_path = compile_pdf(tex_path) if build_pdf else None

    return {
        "survey_code": survey_code,
        "tex_path": str(tex_path),
        "pdf_path": str(pdf_path) if pdf_path else None,
        "pdf_built": pdf_path is not None,
        "tables": len(context["tris_a_plat"]),
        "interviews": context["synthese"]["exploitables"],
    }


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_").lower()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generation de rapports LaTeX")
    parser.add_argument("--survey-code", required=True, help="Code de l'enquete, ex. ECVM2026")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--no-pdf", action="store_true", help="Produire le .tex sans compiler")
    args = parser.parse_args(argv)

    try:
        result = generate(args.survey_code, args.output_dir, build_pdf=not args.no_pdf)
    except WarehouseEmpty as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1

    print(f"Source LaTeX : {result['tex_path']}")
    if result["pdf_built"]:
        print(f"PDF          : {result['pdf_path']}")
    else:
        print("PDF          : non compile (moteur LaTeX absent ou option --no-pdf)")
    print(f"Tableaux     : {result['tables']}  |  entretiens : {result['interviews']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
