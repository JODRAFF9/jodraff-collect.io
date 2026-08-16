"""Chaine complete : collecte -> bronze -> silver -> gold -> controles -> rapport.

Ce test est le garde-fou de l'ensemble : il verifie qu'une donnee saisie sur le
terrain arrive intacte jusqu'au tableau de restitution, et que les regles de
confidentialite tiennent sur tout le parcours.
"""

from __future__ import annotations

import pytest

from apps.api.models import Questionnaire, Quota, SurveyStatus, UserRole
from apps.api.services import collect as collect_service
from apps.api.services import questionnaire as qs
from data_platform import bronze, quality
from data_platform.runner import connect_warehouse, export_gold, run_bronze, run_layer

PII_SCHEMA = {
    "title": "Questionnaire avec donnee personnelle",
    "choice_lists": [
        {
            "code": "sexe",
            "name": "Sexe",
            "choices": [
                {"code": "F", "label": {"fr": "Femme"}, "order": 1},
                {"code": "M", "label": {"fr": "Homme"}, "order": 2},
            ],
        }
    ],
    "sections": [
        {
            "code": "s1",
            "label": {"fr": "Section"},
            "order": 1,
            "questions": [
                {
                    "code": "nom_complet",
                    "type": "text",
                    "label": {"fr": "Nom et prenom du repondant"},
                    "order": 1,
                    "is_pii": True,
                },
                {
                    "code": "sexe",
                    "type": "single_choice",
                    "label": {"fr": "Sexe"},
                    "order": 2,
                    "required": True,
                    "choice_list": "sexe",
                    "analysis_role": "dimension",
                },
                {
                    "code": "revenu",
                    "type": "decimal",
                    "label": {"fr": "Revenu mensuel"},
                    "order": 3,
                    "analysis_role": "mesure",
                },
            ],
        }
    ],
}


@pytest.fixture
def enquete_collectee(session, org, users, survey):
    """Enquete dont le questionnaire porte une donnee personnelle, et collectee."""
    survey.status = SurveyStatus.DESIGN.value
    version = max(q.version for q in survey.questionnaires) + 1
    questionnaire = Questionnaire(survey_id=survey.id, version=version, title="V2")
    session.add(questionnaire)
    session.flush()
    qs.from_schema(session, questionnaire, PII_SCHEMA)
    qs.publish(session, questionnaire)
    survey.status = SurveyStatus.ACTIVE.value

    session.add(
        Quota(survey_id=survey.id, label="Femmes", dimensions={"sexe": "F"}, target=5)
    )
    session.flush()

    enqueteur = users[UserRole.ENUMERATOR.value]
    donnees = [
        ("Awa Diop", "F", 250000.0),
        ("Moussa Fall", "M", 180000.0),
        ("Fatou Sow", "F", 320000.0),
        ("Ibrahima Ba", "M", 95000.0),
    ]
    for index, (nom, sexe, revenu) in enumerate(donnees):
        entretien = collect_service.start_interview(
            session,
            survey,
            "CAPI",
            enumerator_id=enqueteur.id,
            client_uuid=f"pipeline-{index}",
            latitude=14.7,
            longitude=-17.4,
        )
        collect_service.save_answer(session, entretien, "nom_complet", nom)
        collect_service.save_answer(session, entretien, "sexe", sexe)
        collect_service.save_answer(session, entretien, "revenu", revenu)
        collect_service.complete_interview(session, entretien)

    session.commit()
    return survey


@pytest.fixture
def entrepot_construit(enquete_collectee):
    """Execute le pipeline complet et libere l'entrepot.

    La connexion est refermee avant de rendre la main : DuckDB refuse deux
    connexions de modes differents sur un meme fichier, et le generateur de
    rapports ouvre le sien en lecture seule.
    """
    etape = run_bronze(full_refresh=True)
    assert etape.status == "success", etape.detail

    connexion = connect_warehouse()
    try:
        bronze.register_bronze_views(connexion)
        for couche in ("silver", "gold"):
            resultat = run_layer(connexion, couche)
            assert resultat.status == "success", resultat.detail
        export_gold(connexion)
    finally:
        connexion.close()

    return enquete_collectee


@pytest.fixture
def entrepot(entrepot_construit):
    """Connexion en lecture seule a l'entrepot construit."""
    connexion = connect_warehouse(read_only=True)
    yield connexion
    connexion.close()


class TestBronze:
    def test_toutes_les_tables_sont_extraites(self, enquete_collectee):
        resultats = run_bronze(full_refresh=True)
        assert resultats.status == "success"
        assert resultats.detail["per_table"]["interviews"] == 4
        assert resultats.detail["per_table"]["answers"] == 12

    def test_extraction_incrementale_ne_rejoue_pas_tout(self, enquete_collectee):
        run_bronze(full_refresh=True)
        second = run_bronze(full_refresh=False)
        # Les tables incrementales n'ont plus rien a remonter au second passage.
        assert second.detail["per_table"]["answers"] == 0


class TestSilverEtGold:
    def test_entretiens_arrivent_en_gold(self, entrepot):
        assert entrepot.execute("SELECT COUNT(*) FROM fact_interview").fetchone()[0] == 4

    def test_reponses_arrivent_en_gold(self, entrepot):
        assert entrepot.execute("SELECT COUNT(*) FROM fact_answer").fetchone()[0] == 12

    def test_dimension_question_couvre_toutes_les_versions(self, entrepot):
        """La dimension conserve les questions de chaque version publiee.

        C'est ce qui permet de reinterpreter des donnees anciennes avec
        l'instrument qui les a reellement produites.
        """
        codes_v2 = {
            row[0]
            for row in entrepot.execute(
                "SELECT question_code FROM dim_question WHERE questionnaire_version = 2"
            ).fetchall()
        }
        assert codes_v2 == {"nom_complet", "sexe", "revenu"}

        versions = {
            row[0]
            for row in entrepot.execute(
                "SELECT DISTINCT questionnaire_version FROM dim_question"
            ).fetchall()
        }
        assert versions == {1, 2}

    def test_valeurs_numeriques_preservees(self, entrepot):
        """La donnee saisie doit traverser trois couches sans se deformer."""
        total = entrepot.execute(
            "SELECT SUM(value_number) FROM fact_answer WHERE question_code = 'revenu'"
        ).fetchone()[0]
        assert total == pytest.approx(845000.0)

    def test_libelles_de_modalite_restitues(self, entrepot):
        libelles = {
            row[0]
            for row in entrepot.execute(
                "SELECT DISTINCT value_label FROM fact_answer WHERE question_code = 'sexe'"
            ).fetchall()
        }
        assert libelles == {"Femme", "Homme"}

    def test_dimension_date_couvre_la_collecte(self, entrepot):
        orphelins = entrepot.execute(
            "SELECT COUNT(*) FROM fact_interview f "
            "LEFT JOIN dim_date d ON d.date_key = f.date_key WHERE d.date_key IS NULL"
        ).fetchone()[0]
        assert orphelins == 0

    def test_quotas_remontes(self, entrepot):
        cible, realise = entrepot.execute(
            "SELECT quota_target, quota_achieved FROM fact_quota"
        ).fetchone()
        assert cible == 5
        assert realise == 2  # deux repondantes


class TestConfidentialite:
    def test_donnee_personnelle_absente_du_gold(self, entrepot):
        """Un nom saisi sur le terrain ne doit jamais atteindre la restitution."""
        fuites = entrepot.execute(
            "SELECT COUNT(*) FROM fact_answer f JOIN dim_question q "
            "ON q.question_key = f.question_key "
            "WHERE q.is_pii AND (f.value_text IS NOT NULL OR f.value_label IS NOT NULL)"
        ).fetchone()[0]
        assert fuites == 0

    def test_aucun_nom_en_clair_dans_le_gold(self, entrepot):
        noms = entrepot.execute(
            "SELECT COUNT(*) FROM fact_answer WHERE value_text IN "
            "('Awa Diop', 'Moussa Fall', 'Fatou Sow', 'Ibrahima Ba')"
        ).fetchone()[0]
        assert noms == 0

    def test_la_reponse_reste_comptee_malgre_le_masquage(self, entrepot):
        """Masquer la valeur ne doit pas faire disparaitre l'observation."""
        lignes = entrepot.execute(
            "SELECT COUNT(*) FROM fact_answer WHERE question_code = 'nom_complet'"
        ).fetchone()[0]
        assert lignes == 4


class TestControlesQualite:
    def test_tous_les_controles_bloquants_passent(self, entrepot):
        resultats = quality.run_checks(entrepot)
        echecs = [r for r in resultats if not r["passed"] and r["severity"] == "error"]
        assert echecs == [], echecs

    def test_synthese_declare_publiable(self, entrepot):
        synthese = quality.summarise(quality.run_checks(entrepot))
        assert synthese["publishable"] is True
        assert synthese["total"] >= 15


class TestMartsDeRestitution:
    def test_tri_a_plat_pondere(self, entrepot):
        lignes = entrepot.execute(
            "SELECT modality, n_raw, pct_weighted FROM mart_frequency_table "
            "WHERE question_code = 'sexe' ORDER BY modality"
        ).fetchall()
        assert {ligne[0] for ligne in lignes} == {"Femme", "Homme"}
        assert sum(ligne[2] for ligne in lignes) == pytest.approx(100.0)

    def test_statistiques_descriptives(self, entrepot):
        ligne = entrepot.execute(
            "SELECT n, mean, median FROM mart_numeric_summary WHERE question_code = 'revenu'"
        ).fetchone()
        assert ligne[0] == 4
        assert ligne[1] == pytest.approx(211250.0)

    def test_avancement_de_la_collecte(self, entrepot):
        total, exploitables = entrepot.execute(
            "SELECT interviews_total, interviews_usable FROM mart_collection_progress"
        ).fetchone()
        assert total == 4
        assert exploitables == 4


class TestRapport:
    def test_generation_du_source_latex(self, entrepot_construit, tmp_path):
        from reports.generate import generate

        enquete = entrepot_construit
        resultat = generate(enquete.code, output_dir=tmp_path, build_pdf=False)
        source = (tmp_path / f"rapport_{enquete.code.lower()}.tex").read_text(encoding="utf-8")

        assert resultat["interviews"] == 4
        assert r"\begin{document}" in source
        assert r"\end{document}" in source
        assert enquete.title in source

    def test_le_rapport_ne_contient_aucun_nom(self, entrepot_construit, tmp_path):
        """Le controle de confidentialite doit tenir jusqu'au document diffuse."""
        from reports.generate import generate

        enquete = entrepot_construit
        generate(enquete.code, output_dir=tmp_path, build_pdf=False)
        source = (tmp_path / f"rapport_{enquete.code.lower()}.tex").read_text(encoding="utf-8")
        for nom in ("Awa Diop", "Moussa Fall", "Fatou Sow", "Ibrahima Ba"):
            assert nom not in source

    def test_echappement_latex(self):
        from reports.generate import escape_tex

        assert escape_tex("Coût & marge 100%") == r"Coût \& marge 100\%"
        assert escape_tex("a_b") == r"a\_b"
        assert escape_tex(None) == ""
