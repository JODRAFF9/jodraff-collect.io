"""Contrats de donnees entre la couche operationnelle et le lakehouse.

Chaque contrat decrit une table source extraite vers la couche bronze : les
colonnes retenues, leur type cible, la cle primaire, la strategie de
chargement et les colonnes porteuses de donnees personnelles.

Ces contrats sont le point de rupture entre l'application et l'entrepot : la
data platform ne lit jamais directement le modele ORM, elle lit ce contrat.
Une evolution du modele qui casse un contrat est detectee a l'extraction et
non trois couches plus loin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LoadStrategy(str, Enum):
    FULL = "full"  # rechargement complet (petites tables de reference)
    INCREMENTAL = "incremental"  # ajout sur la base d'une colonne de curseur


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # type DuckDB / SQL cible
    nullable: bool = True
    is_pii: bool = False
    description: str = ""


@dataclass(frozen=True)
class TableContract:
    name: str  # nom de la table bronze
    source_table: str  # table OLTP d'origine
    primary_key: tuple[str, ...]
    columns: tuple[Column, ...]
    strategy: LoadStrategy = LoadStrategy.FULL
    cursor_column: str | None = None  # colonne de curseur si incremental
    description: str = ""
    json_columns: tuple[str, ...] = field(default_factory=tuple)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def pii_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.is_pii]

    def duckdb_schema(self) -> str:
        """Definition de colonnes utilisable dans un CREATE TABLE."""
        return ", ".join(f'"{c.name}" {c.type}' for c in self.columns)


CONTRACTS: tuple[TableContract, ...] = (
    TableContract(
        name="surveys",
        source_table="surveys",
        primary_key=("id",),
        description="Referentiel des enquetes et de leur dispositif de collecte.",
        json_columns=("collection_services", "languages"),
        columns=(
            Column("id", "VARCHAR", nullable=False),
            Column("org_id", "VARCHAR", nullable=False),
            Column("code", "VARCHAR", nullable=False),
            Column("title", "VARCHAR", nullable=False),
            Column("description", "VARCHAR"),
            Column("status", "VARCHAR", nullable=False),
            Column("collection_services", "VARCHAR", description="Tableau JSON des modes actives"),
            Column("target_sample", "BIGINT"),
            Column("start_date", "DATE"),
            Column("end_date", "DATE"),
            Column("default_language", "VARCHAR"),
            Column("languages", "VARCHAR"),
            Column("created_at", "TIMESTAMP"),
            Column("updated_at", "TIMESTAMP"),
        ),
    ),
    TableContract(
        name="questions",
        source_table="questions",
        primary_key=("id",),
        description="Metadonnees des questions : la dimension d'analyse des reponses.",
        json_columns=("label", "hint", "only_services", "constraint_message"),
        columns=(
            Column("id", "VARCHAR", nullable=False),
            Column("section_id", "VARCHAR", nullable=False),
            Column("code", "VARCHAR", nullable=False),
            Column("type", "VARCHAR", nullable=False),
            Column("label", "VARCHAR"),
            Column("hint", "VARCHAR"),
            Column("order_index", "BIGINT"),
            Column("is_required", "BOOLEAN"),
            Column("relevance", "VARCHAR"),
            Column("min_value", "DOUBLE"),
            Column("max_value", "DOUBLE"),
            Column("choice_list_id", "VARCHAR"),
            Column("only_services", "VARCHAR"),
            Column("is_pii", "BOOLEAN", description="Marque la variable comme donnee personnelle"),
            Column("analysis_role", "VARCHAR"),
        ),
    ),
    TableContract(
        name="sections",
        source_table="sections",
        primary_key=("id",),
        description="Sections du questionnaire, pour le regroupement d'analyse.",
        json_columns=("label", "description"),
        columns=(
            Column("id", "VARCHAR", nullable=False),
            Column("questionnaire_id", "VARCHAR", nullable=False),
            Column("code", "VARCHAR", nullable=False),
            Column("label", "VARCHAR"),
            Column("order_index", "BIGINT"),
            Column("is_repeatable", "BOOLEAN"),
        ),
    ),
    TableContract(
        name="choices",
        source_table="choices",
        primary_key=("id",),
        description="Modalites de reponse : libelles restitues dans Power BI.",
        json_columns=("label",),
        columns=(
            Column("id", "VARCHAR", nullable=False),
            Column("choice_list_id", "VARCHAR", nullable=False),
            Column("code", "VARCHAR", nullable=False),
            Column("label", "VARCHAR"),
            Column("order_index", "BIGINT"),
            Column("score", "DOUBLE"),
        ),
    ),
    TableContract(
        name="questionnaires",
        source_table="questionnaires",
        primary_key=("id",),
        description="Versions de questionnaire, cle de tracabilite de l'instrument.",
        json_columns=("settings",),
        columns=(
            Column("id", "VARCHAR", nullable=False),
            Column("survey_id", "VARCHAR", nullable=False),
            Column("version", "BIGINT"),
            Column("title", "VARCHAR"),
            Column("status", "VARCHAR"),
            Column("schema_hash", "VARCHAR"),
            Column("published_at", "TIMESTAMP"),
        ),
    ),
    TableContract(
        name="interviews",
        source_table="interviews",
        primary_key=("id",),
        strategy=LoadStrategy.INCREMENTAL,
        cursor_column="updated_at",
        description="Fait principal : un entretien realise, quel que soit le mode.",
        json_columns=("quality_flags",),
        columns=(
            Column("id", "VARCHAR", nullable=False),
            Column("survey_id", "VARCHAR", nullable=False),
            Column("questionnaire_id", "VARCHAR", nullable=False),
            Column("assignment_id", "VARCHAR"),
            Column("enumerator_id", "VARCHAR"),
            Column("collection_service", "VARCHAR", nullable=False),
            Column("status", "VARCHAR", nullable=False),
            Column("language", "VARCHAR"),
            Column("started_at", "TIMESTAMP"),
            Column("ended_at", "TIMESTAMP"),
            Column("duration_seconds", "BIGINT"),
            Column("submitted_at", "TIMESTAMP"),
            Column("latitude", "DOUBLE", is_pii=True),
            Column("longitude", "DOUBLE", is_pii=True),
            Column("gps_accuracy_m", "DOUBLE"),
            Column("device_id", "VARCHAR"),
            Column("app_version", "VARCHAR"),
            Column("validated_by", "VARCHAR"),
            Column("validated_at", "TIMESTAMP"),
            Column("quality_score", "DOUBLE"),
            Column("quality_flags", "VARCHAR"),
            Column("sync_batch_id", "VARCHAR"),
            Column("created_at", "TIMESTAMP"),
            Column("updated_at", "TIMESTAMP"),
        ),
    ),
    TableContract(
        name="answers",
        source_table="answers",
        primary_key=("id",),
        strategy=LoadStrategy.INCREMENTAL,
        cursor_column="answered_at",
        description="Reponses en format long : le grain le plus fin de la collecte.",
        json_columns=("value_json",),
        columns=(
            Column("id", "VARCHAR", nullable=False),
            Column("interview_id", "VARCHAR", nullable=False),
            Column("question_id", "VARCHAR"),
            Column("question_code", "VARCHAR", nullable=False),
            Column("question_type", "VARCHAR", nullable=False),
            Column("repeat_index", "BIGINT"),
            Column("value_text", "VARCHAR", is_pii=True),
            Column("value_number", "DOUBLE"),
            Column("value_date", "TIMESTAMP"),
            Column("value_json", "VARCHAR"),
            Column("is_other", "BOOLEAN"),
            Column("answered_at", "TIMESTAMP"),
        ),
    ),
    TableContract(
        name="assignments",
        source_table="assignments",
        primary_key=("id",),
        strategy=LoadStrategy.INCREMENTAL,
        cursor_column="updated_at",
        description="Affectations terrain : base du calcul de rendement.",
        columns=(
            Column("id", "VARCHAR", nullable=False),
            Column("survey_id", "VARCHAR", nullable=False),
            Column("sample_unit_id", "VARCHAR", nullable=False),
            Column("enumerator_id", "VARCHAR"),
            Column("collection_service", "VARCHAR"),
            Column("status", "VARCHAR"),
            Column("due_date", "DATE"),
            Column("priority", "BIGINT"),
            Column("attempts", "BIGINT"),
            Column("outcome_code", "VARCHAR"),
            Column("created_at", "TIMESTAMP"),
            Column("updated_at", "TIMESTAMP"),
        ),
    ),
    TableContract(
        name="sample_units",
        source_table="sample_units",
        primary_key=("id",),
        description="Echantillon : geographie, strate et poids de sondage.",
        json_columns=("attributes",),
        columns=(
            Column("id", "VARCHAR", nullable=False),
            Column("survey_id", "VARCHAR", nullable=False),
            Column("code", "VARCHAR", nullable=False),
            Column("contact_name", "VARCHAR", is_pii=True),
            Column("phone", "VARCHAR", is_pii=True),
            Column("email", "VARCHAR", is_pii=True),
            Column("address", "VARCHAR", is_pii=True),
            Column("geo_level_1", "VARCHAR"),
            Column("geo_level_2", "VARCHAR"),
            Column("geo_level_3", "VARCHAR"),
            Column("latitude", "DOUBLE", is_pii=True),
            Column("longitude", "DOUBLE", is_pii=True),
            Column("stratum", "VARCHAR"),
            Column("sampling_weight", "DOUBLE"),
            Column("attributes", "VARCHAR"),
        ),
    ),
    TableContract(
        name="enumerators",
        source_table="enumerator_profiles",
        primary_key=("id",),
        description="Profils enqueteurs : zone, capacite, habilitations.",
        json_columns=("certified_services",),
        columns=(
            Column("id", "VARCHAR", nullable=False),
            Column("user_id", "VARCHAR", nullable=False),
            Column("supervisor_id", "VARCHAR"),
            Column("matricule", "VARCHAR"),
            Column("base_zone", "VARCHAR"),
            Column("device_id", "VARCHAR"),
            Column("hired_at", "DATE"),
            Column("daily_capacity", "BIGINT"),
            Column("certified_services", "VARCHAR"),
            Column("training_completed", "BOOLEAN"),
            Column("status", "VARCHAR"),
        ),
    ),
    TableContract(
        name="users",
        source_table="users",
        primary_key=("id",),
        description="Comptes : uniquement les attributs necessaires a l'analyse.",
        columns=(
            Column("id", "VARCHAR", nullable=False),
            Column("org_id", "VARCHAR", nullable=False),
            Column("full_name", "VARCHAR", is_pii=True),
            Column("role", "VARCHAR"),
            Column("is_active", "BOOLEAN"),
            Column("created_at", "TIMESTAMP"),
        ),
    ),
    TableContract(
        name="paradata",
        source_table="paradata",
        primary_key=("id",),
        strategy=LoadStrategy.INCREMENTAL,
        cursor_column="occurred_at",
        description="Traces d'execution : detection des comportements atypiques.",
        json_columns=("payload",),
        columns=(
            Column("id", "VARCHAR", nullable=False),
            Column("interview_id", "VARCHAR", nullable=False),
            Column("event_type", "VARCHAR", nullable=False),
            Column("question_code", "VARCHAR"),
            Column("occurred_at", "TIMESTAMP"),
            Column("duration_ms", "BIGINT"),
        ),
    ),
    TableContract(
        name="quotas",
        source_table="quotas",
        primary_key=("id",),
        description="Suivi des quotas par croisement de variables.",
        json_columns=("dimensions",),
        columns=(
            Column("id", "VARCHAR", nullable=False),
            Column("survey_id", "VARCHAR", nullable=False),
            Column("label", "VARCHAR"),
            Column("dimensions", "VARCHAR"),
            Column("target", "BIGINT"),
            Column("achieved", "BIGINT"),
            Column("is_blocking", "BOOLEAN"),
        ),
    ),
)


CONTRACTS_BY_NAME: dict[str, TableContract] = {c.name: c for c in CONTRACTS}


def get_contract(name: str) -> TableContract:
    try:
        return CONTRACTS_BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"Contrat inconnu : {name}") from exc


def all_pii_columns() -> dict[str, list[str]]:
    """Cartographie des donnees personnelles, pour l'anonymisation en silver."""
    return {c.name: c.pii_columns for c in CONTRACTS if c.pii_columns}
