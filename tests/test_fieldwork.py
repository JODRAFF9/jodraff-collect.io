"""Gestion des enqueteurs : habilitations, affectation, charge, supervision."""

from __future__ import annotations

import pytest

from apps.api.models import (
    Assignment,
    AssignmentStatus,
    EnumeratorProfile,
    SampleUnit,
    User,
    UserRole,
)
from apps.api.security import hash_password
from apps.api.services import fieldwork as fw


def _creer_enqueteur(session, org, matricule, zone, services, capacite=8, forme=True):
    account = User(
        org_id=org.id,
        email=f"{matricule.lower()}@test.local",
        full_name=f"Enqueteur {matricule}",
        role=UserRole.ENUMERATOR.value,
        password_hash=hash_password("motdepasse123"),
    )
    session.add(account)
    session.flush()
    session.add(
        EnumeratorProfile(
            user_id=account.id,
            matricule=matricule,
            base_zone=zone,
            daily_capacity=capacite,
            certified_services=services,
            training_completed=forme,
        )
    )
    session.flush()
    return account


def _charger_echantillon(session, survey, repartition, service="CAPI"):
    """repartition : {zone: nombre d'unites}."""
    index = 0
    for zone, nombre in repartition.items():
        for _ in range(nombre):
            index += 1
            unite = SampleUnit(survey_id=survey.id, code=f"U{index:04d}", geo_level_1=zone)
            session.add(unite)
            session.flush()
            session.add(
                Assignment(
                    survey_id=survey.id,
                    sample_unit_id=unite.id,
                    collection_service=service,
                    status=AssignmentStatus.PENDING.value,
                )
            )
    session.flush()


class TestEligibilite:
    def test_enqueteur_non_forme_exclu(self, session, org, survey):
        _creer_enqueteur(session, org, "ENQ100", "Dakar", ["CAPI"], forme=False)
        eligibles = fw.eligible_enumerators(session, survey, "CAPI")
        assert all(u.email != "enq100@test.local" for u in eligibles)

    def test_habilitation_par_mode_respectee(self, session, org, survey):
        _creer_enqueteur(session, org, "ENQ101", "Dakar", ["CATI"])
        eligibles = fw.eligible_enumerators(session, survey, "CAPI")
        assert all(u.email != "enq101@test.local" for u in eligibles)

    def test_liste_vide_signifie_polyvalent(self, session, org, survey):
        _creer_enqueteur(session, org, "ENQ102", "Dakar", [])
        eligibles = fw.eligible_enumerators(session, survey, "CAPI")
        assert any(u.email == "enq102@test.local" for u in eligibles)


class TestAffectation:
    def test_affectation_respecte_la_zone(self, session, org, survey, users):
        _creer_enqueteur(session, org, "ENQ200", "Dakar", ["CAPI"])
        _creer_enqueteur(session, org, "ENQ201", "Thies", ["CAPI"])
        _charger_echantillon(session, survey, {"Dakar": 5, "Thies": 5})

        fw.auto_assign(session, survey, "CAPI")

        affectations = session.query(Assignment).filter(
            Assignment.status == AssignmentStatus.ASSIGNED.value
        ).all()
        for affectation in affectations:
            unite = session.get(SampleUnit, affectation.sample_unit_id)
            profil = session.query(EnumeratorProfile).filter_by(
                user_id=affectation.enumerator_id
            ).one()
            assert profil.base_zone == unite.geo_level_1

    def test_affectation_equilibre_la_charge(self, session, org, survey, users):
        _creer_enqueteur(session, org, "ENQ210", "Dakar", ["CAPI"])
        _creer_enqueteur(session, org, "ENQ211", "Dakar", ["CAPI"])
        _charger_echantillon(session, survey, {"Dakar": 10})

        resultat = fw.auto_assign(session, survey, "CAPI")
        charges = list(resultat["per_enumerator"].values())
        assert resultat["assigned"] == 10
        assert max(charges) - min(charges) <= 1

    def test_capacite_limite_le_volume_affecte(self, session, org, survey, users):
        """La capacite declaree borne ce qu'on peut confier sur la periode.

        Deux enqueteurs sont eligibles a Dakar : celui des fixtures (capacite
        journaliere de 8) et celui cree ici (capacite de 1). Sur deux jours, la
        capacite totale est donc de 18, pour 40 unites a couvrir.
        """
        _creer_enqueteur(session, org, "ENQ220", "Dakar", ["CAPI"], capacite=1)
        _charger_echantillon(session, survey, {"Dakar": 40})

        capacite_totale = (8 + 1) * 2
        resultat = fw.auto_assign(session, survey, "CAPI", due_in_days=2)

        assert resultat["assigned"] == capacite_totale
        assert resultat["unassigned"] == 40 - capacite_totale

    def test_aucun_enqueteur_eligible_leve(self, session, survey, users):
        _charger_echantillon(session, survey, {"Dakar": 3})
        # Le seul enqueteur des fixtures est habilite : on le retire du mode.
        profil = session.query(EnumeratorProfile).first()
        profil.certified_services = ["PAPI"]
        session.flush()
        with pytest.raises(fw.FieldworkError, match="Aucun enqueteur eligible"):
            fw.auto_assign(session, survey, "CAPI")

    def test_mode_sans_enqueteur_refuse(self, session, survey, users):
        with pytest.raises(fw.FieldworkError, match="ne mobilise pas"):
            fw.auto_assign(session, survey, "CAWI")

    def test_zone_ignoree_si_demande(self, session, org, survey, users):
        _creer_enqueteur(session, org, "ENQ230", "Dakar", ["CAPI"])
        _charger_echantillon(session, survey, {"Saint-Louis": 4})

        resultat = fw.auto_assign(session, survey, "CAPI", respect_zone=False)
        assert resultat["assigned"] == 4


class TestTransfert:
    def test_transfert_conserve_la_trace(self, session, org, survey, users):
        _creer_enqueteur(session, org, "ENQ300", "Dakar", ["CAPI"])
        destinataire = _creer_enqueteur(session, org, "ENQ301", "Dakar", ["CAPI"])
        _charger_echantillon(session, survey, {"Dakar": 1})
        fw.auto_assign(session, survey, "CAPI")

        affectation = session.query(Assignment).filter(
            Assignment.status == AssignmentStatus.ASSIGNED.value
        ).first()
        # L'equilibrage decide seul du premier attributaire : la trace doit
        # nommer celui qui detenait reellement l'affectation, quel qu'il soit.
        precedent = affectation.enumerator_id

        fw.reassign(session, affectation.id, destinataire.id, "Enqueteur indisponible")

        assert affectation.enumerator_id == destinataire.id
        assert "indisponible" in affectation.notes
        assert (precedent or "non affecte") in affectation.notes

    def test_transfert_d_une_affectation_terminee_refuse(self, session, org, survey, users):
        _charger_echantillon(session, survey, {"Dakar": 1})
        affectation = session.query(Assignment).first()
        affectation.status = AssignmentStatus.DONE.value
        session.flush()
        with pytest.raises(fw.FieldworkError, match="terminee"):
            fw.reassign(session, affectation.id, "peu-importe", "raison")

    def test_affectation_inconnue_leve(self, session):
        with pytest.raises(fw.FieldworkError, match="introuvable"):
            fw.reassign(session, "inexistant", "x", "raison")


class TestPilotage:
    def test_avancement_agrege_les_statuts(self, session, survey, users):
        from apps.api.services import collect as collect_service

        enqueteur = users[UserRole.ENUMERATOR.value]
        for _ in range(3):
            entretien = collect_service.start_interview(
                session, survey, "CAPI", enumerator_id=enqueteur.id
            )
            collect_service.save_answer(session, entretien, "age", 30)
            collect_service.save_answer(session, entretien, "majeur", "oui")
            collect_service.complete_interview(session, entretien)

        avancement = fw.survey_progress(session, survey)
        assert avancement["usable_interviews"] == 3
        assert avancement["target_sample"] == 100
        assert avancement["completion_rate"] == 0.03

    def test_productivite_par_enqueteur(self, session, survey, users):
        from apps.api.services import collect as collect_service

        enqueteur = users[UserRole.ENUMERATOR.value]
        for _ in range(2):
            entretien = collect_service.start_interview(
                session, survey, "CAPI", enumerator_id=enqueteur.id
            )
            collect_service.save_answer(session, entretien, "age", 30)
            collect_service.save_answer(session, entretien, "majeur", "oui")
            collect_service.complete_interview(session, entretien)

        performance = fw.enumerator_performance(session, survey.id)
        assert len(performance) == 1
        assert performance[0]["interviews_completed"] == 2
        assert performance[0]["completion_rate"] == 1.0

    def test_entretiens_signales_remontes(self, session, survey, users):
        from apps.api.services import collect as collect_service

        enqueteur = users[UserRole.ENUMERATOR.value]
        entretien = collect_service.start_interview(
            session, survey, "CAPI", enumerator_id=enqueteur.id
        )
        collect_service.save_answer(session, entretien, "age", 30)
        collect_service.save_answer(session, entretien, "majeur", "oui")
        collect_service.complete_interview(session, entretien)

        # Entretien instantane sans GPS : score necessairement bas.
        signales = fw.flagged_interviews(session, survey.id, threshold=0.9)
        assert any(i["interview_id"] == entretien.id for i in signales)

    def test_charge_individuelle(self, session, org, survey, users):
        _creer_enqueteur(session, org, "ENQ400", "Dakar", ["CAPI"])
        _charger_echantillon(session, survey, {"Dakar": 4})
        fw.auto_assign(session, survey, "CAPI")

        affectation = session.query(Assignment).filter(
            Assignment.status == AssignmentStatus.ASSIGNED.value
        ).first()
        charge = fw.enumerator_workload(session, affectation.enumerator_id)
        assert charge["open_assignments"] >= 1
        assert charge["assignments"]
