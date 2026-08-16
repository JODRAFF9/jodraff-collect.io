"""Catalogue des services de collecte : point d'entree de la plateforme.

Ces routes sont publiques et ne demandent aucune authentification : elles
constituent la vitrine consultee avant meme la creation d'un projet.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from apps.api.schemas import EstimateRequest
from apps.api.services import collection_catalog as catalog

router = APIRouter(prefix="/api/v1/collection-services", tags=["Services de collecte"])


@router.get("", summary="Lister les services de collecte proposes")
def list_collection_services(
    requires_enumerator: bool | None = Query(default=None),
    offline: bool | None = Query(default=None),
    max_cost_index: int | None = Query(default=None, ge=1, le=5),
) -> dict:
    """Retourne le catalogue, avec filtres optionnels sur les capacites.

    C'est la premiere requete de tout parcours : le commanditaire compare les
    modes avant de decider du dispositif de son enquete.
    """
    services = catalog.list_services()

    if requires_enumerator is not None:
        services = [s for s in services if s.capabilities.requires_enumerator is requires_enumerator]
    if offline is not None:
        services = [s for s in services if s.capabilities.offline is offline]
    if max_cost_index is not None:
        services = [s for s in services if s.cost_index <= max_cost_index]

    return {
        "count": len(services),
        "services": [s.to_dict() for s in services],
    }


@router.get("/{code}", summary="Detail d'un service de collecte")
def get_collection_service(code: str) -> dict:
    try:
        return catalog.get_service(code).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/estimate", summary="Estimer la charge terrain d'un dispositif")
def estimate_dispositif(payload: EstimateRequest) -> dict:
    """Estimation indicative avant lancement : cout, contacts, duree, effectifs.

    Permet d'arbitrer entre les modes des la page d'accueil, sans creer
    d'enquete.
    """
    estimate = catalog.estimate(payload.collection_services, payload.sample_size)
    estimate["allowed_question_types"] = sorted(
        catalog.allowed_question_types(payload.collection_services)
    )
    estimate["services"] = payload.collection_services
    return estimate


@router.get("/{code}/question-types", summary="Types de questions autorises par un mode")
def question_types(code: str) -> dict:
    try:
        service = catalog.get_service(code)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"service": service.code.value, "question_types": list(service.allowed_question_types)}
