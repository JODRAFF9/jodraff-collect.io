# Image de l'API operationnelle.
# La generation de rapports LaTeX n'est pas incluse ici : elle tourne dans une
# tache separee, pour ne pas alourdir de plusieurs centaines de megaoctets une
# image qui doit demarrer vite et se repliquer a la demande.

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Les dependances sont installees avant le code, pour que la couche reste en
# cache tant que pyproject.toml ne change pas. Des paquets vides suffisent a
# resoudre le projet a cette etape ; le code reel arrive juste apres. Les
# dependances ne sont ainsi declarees qu'a un seul endroit : pyproject.toml.
COPY pyproject.toml README.md ./
RUN mkdir -p apps data_platform reports platform_core \
    && touch apps/__init__.py data_platform/__init__.py \
             reports/__init__.py platform_core/__init__.py \
    && pip install --upgrade pip \
    && pip install ".[postgres]"

COPY . .

# Enregistre le code reel a la place des paquets vides, sans refaire la
# resolution des dependances.
RUN pip install --no-deps --force-reinstall .

# Execution sans privileges.
RUN useradd --create-home --uid 1000 jodraff \
    && mkdir -p /data/lake /data/reports \
    && chown -R jodraff:jodraff /app /data
USER jodraff

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl --fail http://localhost:8000/health || exit 1

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
