"""Conception : la validation doit empecher qu'un questionnaire incoherent
parte sur le terrain, ou l'erreur coute infiniment plus cher a corriger."""

from __future__ import annotations

import copy

import pytest

from apps.api.models import Questionnaire, QuestionnaireStatus
from apps.api.services import questionnaire as qs
from tests.conftest import SIMPLE_SCHEMA


def _brouillon(session, survey, schema) -> Questionnaire:
    version = max((q.version for q in survey.questionnaires), default=0) + 1
    draft = Questionnaire(survey_id=survey.id, version=version, title="Brouillon")
    session.add(draft)
    session.flush()
    qs.from_schema(session, draft, schema)
    return draft


def _erreurs(issues) -> list[str]:
    return [f"{i.location} {i.message}" for i in issues if i.severity == "error"]


class TestImportExport:
    def test_aller_retour_conserve_la_structure(self, session, survey):
        publie = survey.published_questionnaire
        schema = qs.to_schema(publie)
        assert len(schema["sections"]) == 1
        assert len(schema["sections"][0]["questions"]) == 5
        assert {c["code"] for c in schema["choice_lists"]} == {"oui_non", "usages"}

    def test_empreinte_stable_pour_un_meme_schema(self, session, survey):
        schema = qs.to_schema(survey.published_questionnaire)
        assert qs.schema_hash(schema) == qs.schema_hash(copy.deepcopy(schema))

    def test_empreinte_change_si_le_schema_change(self, session, survey):
        schema = qs.to_schema(survey.published_questionnaire)
        modifie = copy.deepcopy(schema)
        modifie["sections"][0]["questions"][0]["label"] = {"fr": "Autre libelle"}
        assert qs.schema_hash(schema) != qs.schema_hash(modifie)

    def test_libelle_texte_simple_accepte(self, session, survey):
        schema = copy.deepcopy(SIMPLE_SCHEMA)
        schema["sections"][0]["questions"][0]["label"] = "Age du repondant"
        draft = _brouillon(session, survey, schema)
        question = next(q for q in draft.iter_questions() if q.code == "age")
        assert question.label == {"fr": "Age du repondant"}

    def test_questionnaire_publie_non_modifiable(self, session, survey):
        publie = survey.published_questionnaire
        with pytest.raises(ValueError, match="publie"):
            qs.from_schema(session, publie, SIMPLE_SCHEMA)


class TestValidation:
    def test_questionnaire_correct_est_valide(self, session, survey):
        assert _erreurs(qs.validate(survey.published_questionnaire, survey)) == []

    def test_questionnaire_vide_rejete(self, session, survey):
        draft = _brouillon(session, survey, {"title": "Vide", "sections": []})
        assert any("aucune question" in e for e in _erreurs(qs.validate(draft, survey)))

    def test_code_non_identifiant_rejete(self, session, survey):
        """Un code invalide casserait le SQL et Power BI en aval."""
        schema = copy.deepcopy(SIMPLE_SCHEMA)
        schema["sections"][0]["questions"][0]["code"] = "age du chef"
        draft = _brouillon(session, survey, schema)
        assert any("identifiant valide" in e for e in _erreurs(qs.validate(draft, survey)))

    def test_type_incompatible_avec_le_mode_rejete(self, session, survey):
        """Le CATI ne peut pas porter de photo : il faut le detecter avant terrain."""
        schema = copy.deepcopy(SIMPLE_SCHEMA)
        schema["sections"][0]["questions"].append(
            {"code": "photo", "type": "photo", "label": {"fr": "Photo"}, "order": 9}
        )
        draft = _brouillon(session, survey, schema)
        erreurs = _erreurs(qs.validate(draft, survey))
        assert any("CATI" in e for e in erreurs)

    def test_type_restreint_a_un_mode_est_accepte(self, session, survey):
        schema = copy.deepcopy(SIMPLE_SCHEMA)
        schema["sections"][0]["questions"].append(
            {
                "code": "photo",
                "type": "photo",
                "label": {"fr": "Photo"},
                "order": 9,
                "only_services": ["CAPI"],
            }
        )
        draft = _brouillon(session, survey, schema)
        assert _erreurs(qs.validate(draft, survey)) == []

    def test_choix_sans_liste_rejete(self, session, survey):
        schema = copy.deepcopy(SIMPLE_SCHEMA)
        del schema["sections"][0]["questions"][1]["choice_list"]
        draft = _brouillon(session, survey, schema)
        assert any("liste de choix" in e for e in _erreurs(qs.validate(draft, survey)))

    def test_calcul_sans_formule_rejete(self, session, survey):
        schema = copy.deepcopy(SIMPLE_SCHEMA)
        del schema["sections"][0]["questions"][4]["calculation"]
        draft = _brouillon(session, survey, schema)
        assert any("sans formule" in e for e in _erreurs(qs.validate(draft, survey)))

    def test_bornes_incoherentes_rejetees(self, session, survey):
        schema = copy.deepcopy(SIMPLE_SCHEMA)
        schema["sections"][0]["questions"][0]["min"] = 100
        schema["sections"][0]["questions"][0]["max"] = 10
        draft = _brouillon(session, survey, schema)
        assert any("Borne minimale" in e for e in _erreurs(qs.validate(draft, survey)))

    def test_expression_dangereuse_rejetee(self, session, survey):
        schema = copy.deepcopy(SIMPLE_SCHEMA)
        schema["sections"][0]["questions"][1]["relevance"] = "__import__('os')"
        draft = _brouillon(session, survey, schema)
        assert _erreurs(qs.validate(draft, survey))

    def test_reference_a_une_question_posterieure_rejetee(self, session, survey):
        """Une regle ne peut pas dependre d'une reponse pas encore donnee."""
        schema = copy.deepcopy(SIMPLE_SCHEMA)
        schema["sections"][0]["questions"][0]["relevance"] = "revenu > 0"
        draft = _brouillon(session, survey, schema)
        erreurs = _erreurs(qs.validate(draft, survey))
        assert any("plus loin" in e or "Variable inconnue" in e for e in erreurs)

    def test_section_repetable_sans_compteur_rejetee(self, session, survey):
        schema = copy.deepcopy(SIMPLE_SCHEMA)
        schema["sections"][0]["is_repeatable"] = True
        draft = _brouillon(session, survey, schema)
        assert any("comptage" in e for e in _erreurs(qs.validate(draft, survey)))

    def test_contrainte_sans_message_est_un_avertissement(self, session, survey):
        schema = copy.deepcopy(SIMPLE_SCHEMA)
        del schema["sections"][0]["questions"][3]["constraint_message"]
        draft = _brouillon(session, survey, schema)
        issues = qs.validate(draft, survey)
        assert _erreurs(issues) == []
        assert any(i.severity == "warning" for i in issues)


class TestPublication:
    def test_publication_refusee_si_erreurs(self, session, survey):
        schema = copy.deepcopy(SIMPLE_SCHEMA)
        schema["sections"][0]["questions"][0]["code"] = "code invalide"
        draft = _brouillon(session, survey, schema)
        with pytest.raises(ValueError, match="Publication impossible"):
            qs.publish(session, draft)

    def test_publication_calcule_l_empreinte(self, session, survey):
        publie = survey.published_questionnaire
        assert publie.schema_hash and len(publie.schema_hash) == 32

    def test_publication_archive_la_version_precedente(self, session, survey):
        ancienne = survey.published_questionnaire
        nouvelle = _brouillon(session, survey, SIMPLE_SCHEMA)
        qs.publish(session, nouvelle)
        session.refresh(ancienne)
        assert ancienne.status == QuestionnaireStatus.ARCHIVED.value
        assert survey.published_questionnaire.id == nouvelle.id

    def test_nouvelle_version_duplique_le_contenu(self, session, survey):
        source = survey.published_questionnaire
        copie = qs.new_version(session, source)
        assert copie.version == source.version + 1
        assert copie.status == QuestionnaireStatus.DRAFT.value
        assert len(list(copie.iter_questions())) == len(list(source.iter_questions()))


class TestApiQuestionnaire:
    def test_rapport_de_compatibilite(self, client, survey, users):
        from tests.conftest import auth_headers

        headers = auth_headers(client, "methodologist@test.local")
        response = client.get(
            f"/api/v1/surveys/{survey.id}/questionnaires/"
            f"{survey.published_questionnaire.id}/compatibility",
            headers=headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert set(payload["by_service"]) == {"CAPI", "CATI"}
        assert all(s["is_compatible"] for s in payload["by_service"].values())

    def test_validation_via_api(self, client, survey, users):
        from tests.conftest import auth_headers

        headers = auth_headers(client, "methodologist@test.local")
        response = client.post(
            f"/api/v1/surveys/{survey.id}/questionnaires/"
            f"{survey.published_questionnaire.id}/validate",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["is_valid"] is True
