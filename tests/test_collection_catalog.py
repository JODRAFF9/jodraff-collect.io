"""Le catalogue est le point d'entree : son contrat doit rester stable."""

from __future__ import annotations

import pytest

from apps.api.services import collection_catalog as catalog


def test_catalogue_expose_les_six_services():
    codes = {service.code.value for service in catalog.list_services()}
    assert codes == {"CAWI", "CAPI", "CATI", "PAPI", "SMS", "MIXED"}


def test_service_inconnu_leve():
    with pytest.raises(KeyError):
        catalog.get_service("TELEPATHIE")


def test_recherche_insensible_a_la_casse():
    assert catalog.get_service("capi").code.value == "CAPI"


@pytest.mark.parametrize(
    ("code", "hors_ligne", "enqueteur"),
    [
        ("CAPI", True, True),
        ("CAWI", False, False),
        ("CATI", False, True),
        ("SMS", False, False),
    ],
)
def test_capacites_declarees(code, hors_ligne, enqueteur):
    service = catalog.get_service(code)
    assert service.capabilities.offline is hors_ligne
    assert service.capabilities.requires_enumerator is enqueteur


def test_types_de_questions_prend_l_intersection():
    """Un dispositif multimode se concoit pour le mode le plus contraint."""
    types = catalog.allowed_question_types(["CAPI", "SMS"])
    assert "photo" not in types, "Le SMS ne supporte pas la photo"
    assert "geopoint" not in types
    assert "single_choice" in types


def test_types_de_questions_mode_unique():
    types = catalog.allowed_question_types(["CAPI"])
    assert {"photo", "geopoint", "audio"} <= types


def test_requires_enumerator_est_un_ou_logique():
    assert catalog.requires_enumerator(["CAWI", "CAPI"]) is True
    assert catalog.requires_enumerator(["CAWI", "SMS"]) is False


def test_estimation_combine_les_taux_de_reponse():
    """Deux modes sequences font mieux qu'un seul : l'echec doit etre conjoint."""
    seul = catalog.estimate(["CAWI"], 1000)
    combine = catalog.estimate(["CAWI", "CATI"], 1000)
    assert combine["expected_response_rate"] > seul["expected_response_rate"]
    assert combine["contacts_to_mobilise"] < seul["contacts_to_mobilise"]


def test_estimation_suggere_des_enqueteurs_seulement_si_necessaire():
    assert catalog.estimate(["CAWI"], 500)["suggested_enumerators"] == 0
    assert catalog.estimate(["CAPI"], 500)["suggested_enumerators"] > 0


def test_estimation_vide_si_echantillon_nul():
    assert catalog.estimate(["CAPI"], 0) == {}


# --------------------------------------------------------------------------
# Points d'entree HTTP
# --------------------------------------------------------------------------


def test_route_catalogue_est_publique(client):
    """Le catalogue doit etre consultable avant toute authentification."""
    response = client.get("/api/v1/collection-services")
    assert response.status_code == 200
    assert response.json()["count"] == 6


def test_route_catalogue_filtre_sur_les_capacites(client):
    response = client.get("/api/v1/collection-services", params={"offline": True})
    codes = [s["code"] for s in response.json()["services"]]
    assert set(codes) == {"CAPI", "MIXED"}


def test_route_catalogue_filtre_sur_le_cout(client):
    response = client.get("/api/v1/collection-services", params={"max_cost_index": 1})
    codes = {s["code"] for s in response.json()["services"]}
    assert codes == {"CAWI", "SMS"}


def test_route_detail_service(client):
    response = client.get("/api/v1/collection-services/CATI")
    assert response.status_code == 200
    assert response.json()["capabilities"]["call_scheduling"] is True


def test_route_detail_service_inconnu(client):
    assert client.get("/api/v1/collection-services/INEXISTANT").status_code == 404


def test_route_estimation(client):
    response = client.post(
        "/api/v1/collection-services/estimate",
        json={"collection_services": ["CAPI"], "sample_size": 600},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["requires_enumerator"] is True
    assert payload["suggested_enumerators"] >= 1
    assert "photo" in payload["allowed_question_types"]


def test_route_estimation_refuse_un_service_inconnu(client):
    response = client.post(
        "/api/v1/collection-services/estimate",
        json={"collection_services": ["PIGEON"], "sample_size": 100},
    )
    assert response.status_code == 422


def test_page_d_accueil_presente_les_services(client):
    """La page d'accueil est la vitrine du catalogue."""
    response = client.get("/")
    assert response.status_code == 200
    for code in ("CAWI", "CAPI", "CATI", "PAPI", "SMS"):
        assert code in response.text
