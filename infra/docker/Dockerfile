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

# Les dependances sont installees avant le code : la couche est mise en cache
# tant que pyproject.toml ne change pas.
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install "fastapi>=0.115" "uvicorn[standard]>=0.30" "sqlalchemy>=2.0" \
                   "pydantic>=2.7" "pydantic-settings>=2.3" "jinja2>=3.1" \
                   "python-multipart>=0.0.9" "duckdb>=1.0" "email-validator>=2.0" \
                   "psycopg[binary]>=3.1"

COPY . .

# Execution sans privileges.
RUN useradd --create-home --uid 1000 jodraff \
    && mkdir -p /data/lake /data/reports \
    && chown -R jodraff:jodraff /app /data
USER jodraff

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl --fail http://localhost:8000/health || exit 1

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
