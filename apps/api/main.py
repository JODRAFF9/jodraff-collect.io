"""Application FastAPI : couche operationnelle de la plateforme.

Expose le catalogue des services de collecte, la conception des
questionnaires, la realisation de l'enquete, la gestion des enqueteurs et les
points d'entree de supervision. Les donnees produites ici alimentent la couche
bronze du lakehouse (voir ``data_platform``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from apps.api.db import init_db
from apps.api.routers import analytics, auth, catalog, collect, fieldwork, questionnaires, surveys, web
from platform_core.config import settings

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    # En local et en test, le schema est cree au demarrage. En production, il
    # est gere par les scripts DDL versionnes.
    if settings.environment == "local":
        init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Plateforme de bout en bout : conception et implementation des questionnaires, "
        "realisation de l'enquete sur tous les modes de collecte, gestion des enqueteurs, "
        "centralisation SQL, traitement et restitution Power Platform, rapports LaTeX."
    ),
    lifespan=lifespan,
)

app.include_router(catalog.router)
app.include_router(auth.router)
app.include_router(surveys.router)
app.include_router(questionnaires.router)
app.include_router(collect.router)
app.include_router(fieldwork.router)
app.include_router(analytics.router)
app.include_router(web.router)

static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/health", tags=["Technique"], summary="Sonde de disponibilite")
def health() -> dict:
    return {"status": "ok", "environment": settings.environment, "app": settings.app_name}


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:  # noqa: ARG001
    """Traduit les erreurs metier non capturees en reponse 422 lisible."""
    return JSONResponse(status_code=422, content={"detail": str(exc)})
