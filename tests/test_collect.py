"""Moteur de collecte : navigation, controles, quotas, qualite, synchronisation."""

from __future__ import annotations

import pytest

from apps.api.models import (
    Assignment,
    AssignmentStatus,
    InterviewStatus,
    Quota,
    SampleUnit,
    SurveyStatus,
    UserRole,
)
from apps.api.services import collect as collect_service
from apps.api.services import fieldwork as fw


@pytest.fixture
def enqueteur(users):
    return users[UserRole.ENUMERATOR.value]


@pytest.fixture
def interview(session, survey, enqueteur):
    return collect_service.start_interview(
        session, survey, "CAPI", enumerator_id=enqueteur.id, latitude=14.7, longitude=-17.4
    )


class TestOuverture:
    def test_ouverture_nominale(self, session, survey, enqueteur):
        entretien = collect_service.start_interview(
            session, survey, "CAPI", enumerator_id=enqueteur.id
        )
        assert entretien.status == InterviewStatus.IN_PROGRESS.value
        assert entretien.questionnaire_id == survey.published_questionnaire.id

    def test_mode_non_active_refuse(self, session, survey, enqueteur):
        with pytest.raises(collect_service.CollectError, match="n'est pas active"):
            collect_service.start_interview(session, survey, "CAWI", enumerator_id=enqueteur.id)

    def test_mode_inconnu_refuse(self, session, survey, enqueteur):
        with pytest.raises(collect_service.CollectError, match="inconnu"):
            collect_service.start_interview(session, survey, "FUMEE", enumerator_id=enqueteur.id)

    def test_enquete_fermee_refuse(self, session, survey, enqueteur):
        survey.status = SurveyStatus.CLOSED.value
        session.flush()
        with pytest.raises(collect_service.CollectError, match="pas ouverte"):
            collect_service.start_interview(session, survey, "CAPI", enumerator_id=enqueteur.id)

    def test_mode_avec_enqueteur_obligatoire(self, session, survey):
        with pytest.raises(collect_service.CollectError, match="exige un enqueteur"):
            collect_service.start_interview(session, survey, "CAPI", enumerator_id=None)

    def test_idempotence_par_client_uuid(self, session, survey, enqueteur):
        """Un lot rejoue apres coupure reseau ne doit pas creer de doublon."""
        premier = collect_service.start_interview(
            session, survey, "CAPI", enumerator_id=enqueteur.id, client_uuid="abc-123"
        )
        second = collect_service.start_interview(
            session, survey, "CAPI", enumerator_id=enqueteur.id, client_uuid="abc-123"
        )
        assert premier.id == second.id


class TestNavigation:
    def test_premiere_question(self, session, interview):
        questions = collect_service.next_questions(session, interview)
        assert questions[0]["code"] == "age"

    def test_saut_si_regle_non_satisfaite(self, session, interview):
        """Un mineur ne doit pas se voir poser la question reservee aux majeurs."""
        collect_service.save_answer(session, interview, "age", 12)
        codes = [q["code"] for q in collect_service.next_questions(session, interview, limit=10)]
        assert "majeur" not in codes

    def test_question_posee_si_regle_satisfaite(self, session, interview):
        collect_service.save_answer(session, interview, "age", 30)
        codes = [q["code"] for q in collect_service.next_questions(session, interview, limit=10)]
        assert "majeur" in codes

    def test_question_restreinte_a_un_autre_mode_ignoree(self, session, survey, enqueteur):
        entretien = collect_service.start_interview(
            session, survey, "CATI", enumerator_id=enqueteur.id
        )
        questions = collect_service.next_questions(session, entretien, limit=20)
        assert questions  # le questionnaire de test n'a pas de question restreinte

    def test_les_modalites_sont_fournies(self, session, interview):
        collect_service.save_answer(session, interview, "age", 30)
        suivante = collect_service.next_questions(session, interview)[0]
        assert {c["code"] for c in suivante["choices"]} == {"oui", "non"}


class TestControlesDeSaisie:
    def test_type_numerique_exige(self, session, interview):
        with pytest.raises(collect_service.AnswerRejected, match="numerique"):
            collect_service.save_answer(session, interview, "age", "vingt")

    def test_entier_exige(self, session, interview):
        with pytest.raises(collect_service.AnswerRejected, match="entier"):
            collect_service.save_answer(session, interview, "age", 25.5)

    def test_borne_maximale(self, session, interview):
        with pytest.raises(collect_service.AnswerRejected, match="superieure au maximum"):
            collect_service.save_answer(session, interview, "age", 250)

    def test_borne_minimale(self, session, interview):
        with pytest.raises(collect_service.AnswerRejected, match="inferieure au minimum"):
            collect_service.save_answer(session, interview, "age", -5)

    def test_contrainte_personnalisee_et_son_message(self, session, interview):
        collect_service.save_answer(session, interview, "age", 30)
        with pytest.raises(collect_service.AnswerRejected, match="negatif"):
            collect_service.save_answer(session, interview, "revenu", -100)

    def test_modalite_inconnue_refusee(self, session, interview):
        collect_service.save_answer(session, interview, "age", 30)
        with pytest.raises(collect_service.AnswerRejected, match="Modalite"):
            collect_service.save_answer(session, interview, "majeur", "peut-etre")

    def test_modalite_exclusive_non_combinable(self, session, interview):
        collect_service.save_answer(session, interview, "age", 30)
        with pytest.raises(collect_service.AnswerRejected, match="exclusive"):
            collect_service.save_answer(session, interview, "usages", ["a", "aucun"])

    def test_question_inconnue_refusee(self, session, interview):
        with pytest.raises(collect_service.CollectError, match="Question inconnue"):
            collect_service.save_answer(session, interview, "inexistante", 1)

    def test_reecriture_d_une_reponse(self, session, interview):
        collect_service.save_answer(session, interview, "age", 30)
        collect_service.save_answer(session, interview, "age", 40)
        contexte = collect_service.build_context(session, interview)
        assert contexte["age"] == 40


class TestCalculs:
    def test_question_calculee_est_alimentee(self, session, interview):
        collect_service.save_answer(session, interview, "age", 30)
        collect_service.save_answer(session, interview, "revenu", 1000)
        contexte = collect_service.build_context(session, interview)
        assert contexte["revenu_par_an"] == 12000

    def test_calcul_recalcule_apres_correction(self, session, interview):
        collect_service.save_answer(session, interview, "age", 30)
        collect_service.save_answer(session, interview, "revenu", 1000)
        collect_service.save_answer(session, interview, "revenu", 2000)
        contexte = collect_service.build_context(session, interview)
        assert contexte["revenu_par_an"] == 24000


class TestCloture:
    def test_cloture_refusee_si_obligatoire_manquant(self, session, interview):
        with pytest.raises(collect_service.CollectError, match="obligatoires"):
            collect_service.complete_interview(session, interview)

    def test_cloture_partielle_autorisee_explicitement(self, session, interview):
        collect_service.complete_interview(session, interview, allow_partial=True)
        assert interview.status == InterviewStatus.PARTIAL.value

    def test_cloture_complete(self, session, interview):
        collect_service.save_answer(session, interview, "age", 30)
        collect_service.save_answer(session, interview, "majeur", "oui")
        collect_service.complete_interview(session, interview)
        assert interview.status == InterviewStatus.COMPLETED.value
        assert interview.duration_seconds is not None
        assert interview.quality_score is not None

    def test_pas_de_saisie_apres_cloture(self, session, interview):
        collect_service.save_answer(session, interview, "age", 30)
        collect_service.save_answer(session, interview, "majeur", "oui")
        collect_service.complete_interview(session, interview)
        with pytest.raises(collect_service.CollectError, match="cloture"):
            collect_service.save_answer(session, interview, "age", 31)

    def test_obligatoire_non_pertinent_n_est_pas_manquant(self, session, interview):
        """La question reservee aux majeurs ne bloque pas la cloture d'un mineur."""
        collect_service.save_answer(session, interview, "age", 10)
        assert collect_service.missing_required(session, interview) == []


class TestQualite:
    def test_entretien_trop_court_signale(self, session, interview):
        collect_service.save_answer(session, interview, "age", 30)
        collect_service.save_answer(session, interview, "majeur", "oui")
        collect_service.complete_interview(session, interview)
        # Un entretien CAPI cloture en quelques secondes est necessairement suspect.
        assert "duree_anormalement_courte" in interview.quality_flags
        assert interview.quality_score < 1.0

    def test_gps_absent_signale(self, session, survey, enqueteur):
        entretien = collect_service.start_interview(
            session, survey, "CAPI", enumerator_id=enqueteur.id
        )
        collect_service.save_answer(session, entretien, "age", 30)
        collect_service.save_answer(session, entretien, "majeur", "oui")
        collect_service.complete_interview(session, entretien)
        assert "gps_absent" in entretien.quality_flags

    def test_validation_par_le_superviseur(self, session, interview, users):
        collect_service.save_answer(session, interview, "age", 30)
        collect_service.save_answer(session, interview, "majeur", "oui")
        collect_service.complete_interview(session, interview)
        superviseur = users[UserRole.SUPERVISOR.value]
        collect_service.validate_interview(session, interview, superviseur.id, accepted=True)
        assert interview.status == InterviewStatus.VALIDATED.value

    def test_rejet_rouvre_l_affectation(self, session, survey, enqueteur, users):
        unite = SampleUnit(survey_id=survey.id, code="U1", geo_level_1="Dakar")
        session.add(unite)
        session.flush()
        affectation = Assignment(
            survey_id=survey.id,
            sample_unit_id=unite.id,
            enumerator_id=enqueteur.id,
            collection_service="CAPI",
            status=AssignmentStatus.ASSIGNED.value,
        )
        session.add(affectation)
        session.flush()

        entretien = collect_service.start_interview(
            session, survey, "CAPI", enumerator_id=enqueteur.id, assignment_id=affectation.id
        )
        collect_service.save_answer(session, entretien, "age", 30)
        collect_service.save_answer(session, entretien, "majeur", "oui")
        collect_service.complete_interview(session, entretien)
        collect_service.validate_interview(
            session, entretien, users[UserRole.SUPERVISOR.value].id, accepted=False, notes="A refaire"
        )
        session.refresh(affectation)
        assert affectation.status == AssignmentStatus.ASSIGNED.value


class TestQuotas:
    def test_quota_incremente_a_la_cloture(self, session, survey, interview):
        quota = Quota(
            survey_id=survey.id, label="Majeurs", dimensions={"majeur": "oui"}, target=10
        )
        session.add(quota)
        session.flush()

        collect_service.save_answer(session, interview, "age", 30)
        collect_service.save_answer(session, interview, "majeur", "oui")
        collect_service.complete_interview(session, interview)
        session.refresh(quota)
        assert quota.achieved == 1

    def test_quota_non_incremente_si_profil_different(self, session, survey, interview):
        quota = Quota(
            survey_id=survey.id, label="Mineurs", dimensions={"majeur": "non"}, target=10
        )
        session.add(quota)
        session.flush()

        collect_service.save_answer(session, interview, "age", 30)
        collect_service.save_answer(session, interview, "majeur", "oui")
        collect_service.complete_interview(session, interview)
        session.refresh(quota)
        assert quota.achieved == 0

    def test_quota_sature_ferme_le_profil(self, session, survey):
        quota = Quota(
            survey_id=survey.id,
            label="Majeurs",
            dimensions={"majeur": "oui"},
            target=1,
            achieved=1,
        )
        session.add(quota)
        session.flush()
        etat = collect_service.quota_state(session, survey.id, {"majeur": "oui"})
        assert etat["open"] is False
        assert "Majeurs" in etat["blocked_by"]


class TestSynchronisation:
    def test_lot_hors_ligne_integre(self, session, survey, enqueteur):
        resultat = fw.ingest_sync_batch(
            session,
            survey,
            device_id="TAB-001",
            enumerator_id=enqueteur.id,
            interviews_payload=[
                {
                    "client_uuid": "offline-1",
                    "collection_service": "CAPI",
                    "latitude": 14.7,
                    "longitude": -17.4,
                    "answers": {"age": 30, "majeur": "oui"},
                },
                {
                    "client_uuid": "offline-2",
                    "collection_service": "CAPI",
                    "answers": {"age": 45, "majeur": "non"},
                },
            ],
        )
        assert resultat["accepted"] == 2
        assert resultat["rejected"] == 0

    def test_rejeu_du_lot_ne_duplique_pas(self, session, survey, enqueteur):
        lot = [{"client_uuid": "offline-1", "collection_service": "CAPI", "answers": {"age": 30}}]
        fw.ingest_sync_batch(session, survey, "TAB-001", enqueteur.id, lot)
        second = fw.ingest_sync_batch(session, survey, "TAB-001", enqueteur.id, lot)
        assert second["duplicates"] == 1
        assert second["accepted"] == 0

    def test_entretien_sans_uuid_rejete(self, session, survey, enqueteur):
        resultat = fw.ingest_sync_batch(
            session, survey, "TAB-001", enqueteur.id, [{"collection_service": "CAPI"}]
        )
        assert resultat["rejected"] == 1

    def test_un_echec_unitaire_ne_bloque_pas_le_lot(self, session, survey, enqueteur):
        """Une tablette ne doit pas perdre toute sa journee pour un enregistrement."""
        resultat = fw.ingest_sync_batch(
            session,
            survey,
            "TAB-001",
            enqueteur.id,
            [
                {"client_uuid": "ok-1", "collection_service": "CAPI", "answers": {"age": 30}},
                {"client_uuid": "ko-1", "collection_service": "MODE_INEXISTANT"},
                {"client_uuid": "ok-2", "collection_service": "CAPI", "answers": {"age": 40}},
            ],
        )
        assert resultat["accepted"] == 2
        assert resultat["rejected"] == 1
