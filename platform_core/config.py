"""Configuration centrale de la plateforme.

Toutes les couches (API operationnelle, data platform, rapports) lisent leur
configuration ici afin qu'un seul jeu de variables d'environnement pilote
l'ensemble : execution locale (SQLite + DuckDB) ou cloud Azure
(Azure SQL / Fabric + ADLS Gen2).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_prefix=""
    )

    # --- Identite ---------------------------------------------------------
    app_name: str = "Jodraff Collect"
    environment: str = Field(default="local", description="local | dev | prod")
    secret_key: str = Field(default="dev-secret-change-me")
    token_ttl_seconds: int = 60 * 60 * 12

    # --- Base operationnelle (OLTP) ---------------------------------------
    # Local : sqlite. Cloud : postgresql+psycopg://... ou mssql+pyodbc://...
    database_url: str = Field(default=f"sqlite:///{ROOT_DIR / 'var' / 'operational.db'}")
    sql_echo: bool = False

    # --- Lakehouse / entrepot (medallion) ---------------------------------
    # Local : repertoire de fichiers Parquet pilote par DuckDB.
    # Cloud : abfss://lakehouse@<compte>.dfs.core.windows.net
    lake_root: str = Field(default=str(ROOT_DIR / "var" / "lake"))
    warehouse_url: str = Field(default=str(ROOT_DIR / "var" / "warehouse.duckdb"))
    warehouse_dialect: str = Field(default="duckdb", description="duckdb | tsql")

    # --- Rapports LaTeX ---------------------------------------------------
    latex_engine: str = Field(default="pdflatex", description="pdflatex | xelatex | tectonic")
    reports_output_dir: str = Field(default=str(ROOT_DIR / "var" / "reports"))

    # --- Divers -----------------------------------------------------------
    default_language: str = "fr"
    page_size: int = 50

    @property
    def lake_path(self) -> Path:
        return Path(self.lake_root)

    def layer_path(self, layer: str) -> Path:
        """Repertoire d'une couche medallion (bronze / silver / gold)."""
        return self.lake_path / layer


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    for sub in ("bronze", "silver", "gold"):
        settings.layer_path(sub).mkdir(parents=True, exist_ok=True)
    Path(settings.reports_output_dir).mkdir(parents=True, exist_ok=True)
    (ROOT_DIR / "var").mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
