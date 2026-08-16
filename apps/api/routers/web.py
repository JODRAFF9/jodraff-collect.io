"""Interface web : accueil du catalogue et parcours de selection."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from apps.api.services import collection_catalog as catalog

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["Interface"], include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Page d'accueil : le catalogue des services de collecte.

    C'est l'ecran d'entree de la plateforme. Le visiteur compare les modes,
    simule la charge terrain, puis engage la conception de son enquete.
    """
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "services": [s.to_dict() for s in catalog.list_services()],
            "title": "Choisir un service de collecte",
        },
    )


@router.get("/services/{code}", response_class=HTMLResponse)
def service_detail(request: Request, code: str) -> HTMLResponse:
    try:
        service = catalog.get_service(code)
    except KeyError:
        return HTMLResponse("<h1>Service inconnu</h1>", status_code=404)
    return templates.TemplateResponse(
        request,
        "service_detail.html",
        {"service": service.to_dict(), "title": service.name},
    )
