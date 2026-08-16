"""Couche bronze : extraction fidele de la base operationnelle vers le lakehouse.

Principes retenus :

* fidelite : aucune transformation metier, on copie la source telle quelle ;
* tracabilite : chaque fichier porte sa date d'ingestion et son lot ;
* idempotence : rejouer une extraction ecrase la partition du jour, jamais
  les partitions anterieures ;
* portabilite : le format de sortie est Parquet, lisible par DuckDB en local
  comme par Fabric, Synapse ou Databricks dans le cloud.
"""

from __future__ import annotations

import csv
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy import text

from data_platform.contracts import CONTRACTS, LoadStrategy, TableContract
from platform_core.config import settings

# Marqueur de valeur manquante dans le fichier CSV de transit. Il doit se
# distinguer d'une chaine vide, qui est une valeur legitime en base.
_NULL_SENTINEL = "\\N"


@dataclass
class IngestionResult:
    table: str
    rows: int
    path: str
    strategy: str
    cursor_value: str | None = None

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "rows": self.rows,
            "path": self.path,
            "strategy": self.strategy,
            "cursor_value": self.cursor_value,
        }


def _watermark_file() -> Path:
    return settings.layer_path("bronze") / "_watermarks.json"


def read_watermarks() -> dict[str, str]:
    """Derniere valeur de curseur ingeree, par table."""
    path = _watermark_file()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_watermarks(watermarks: dict[str, str]) -> None:
    _watermark_file().write_text(
        json.dumps(watermarks, indent=2, sort_keys=True), encoding="utf-8"
    )


def _normalise(value: Any, is_json: bool) -> Any:
    """Convertit une valeur ORM en valeur scalaire ecrivable en Parquet."""
    if value is None:
        return None
    if is_json:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return value
    if isinstance(value, (datetime, date)):
        return value
    return value


def extract_table(
    connection,
    contract: TableContract,
    ingest_date: date,
    batch_id: str,
    watermark: str | None = None,
) -> IngestionResult:
    """Extrait une table source vers une partition Parquet de la couche bronze."""
    columns = contract.column_names
    quoted = ", ".join(f'"{c}"' for c in columns)
    query = f"SELECT {quoted} FROM {contract.source_table}"  # noqa: S608 - noms issus du contrat
    params: dict[str, Any] = {}

    if contract.strategy is LoadStrategy.INCREMENTAL and contract.cursor_column and watermark:
        query += f' WHERE "{contract.cursor_column}" > :watermark'
        params["watermark"] = watermark

    rows = connection.execute(text(query), params).fetchall()

    partition_dir = (
        settings.layer_path("bronze") / contract.name / f"ingest_date={ingest_date.isoformat()}"
    )
    partition_dir.mkdir(parents=True, exist_ok=True)
    target = partition_dir / "data.parquet"

    json_cols = set(contract.json_columns)
    ingested_at = datetime.now(UTC).replace(tzinfo=None)

    con = duckdb.connect()
    staging_csv: Path | None = None
    try:
        con.execute(
            f"CREATE TABLE staging ({contract.duckdb_schema()}, "
            "_ingested_at TIMESTAMP, _batch_id VARCHAR, _source_system VARCHAR)"
        )

        if rows:
            # Passage par un fichier CSV de transit plutot que par des INSERT
            # parametres : le lecteur CSV vectorise de DuckDB est environ
            # soixante fois plus rapide que la liaison de parametres ligne a
            # ligne, qui replanifie chaque instruction.
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv", newline="", encoding="utf-8", delete=False
            ) as handle:
                staging_csv = Path(handle.name)
                writer = csv.writer(handle)
                for row in rows:
                    values = [
                        _normalise(value, col in json_cols)
                        for col, value in zip(columns, row, strict=True)
                    ]
                    values.extend([ingested_at, batch_id, "jodraff_oltp"])
                    writer.writerow([_NULL_SENTINEL if v is None else v for v in values])

            column_spec = ", ".join(
                [f"'{c.name}': '{c.type}'" for c in contract.columns]
                + [
                    "'_ingested_at': 'TIMESTAMP'",
                    "'_batch_id': 'VARCHAR'",
                    "'_source_system': 'VARCHAR'",
                ]
            )
            con.execute(
                f"INSERT INTO staging SELECT * FROM read_csv('{staging_csv.as_posix()}', "
                f"header=false, columns={{{column_spec}}}, nullstr='{_NULL_SENTINEL}')"
            )

        con.execute(
            f"COPY staging TO '{target.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        con.close()
        if staging_csv is not None:
            staging_csv.unlink(missing_ok=True)

    new_watermark = None
    if contract.cursor_column and rows:
        cursor_index = columns.index(contract.cursor_column)
        values = [r[cursor_index] for r in rows if r[cursor_index] is not None]
        if values:
            new_watermark = max(values)
            new_watermark = (
                new_watermark.isoformat() if hasattr(new_watermark, "isoformat") else str(new_watermark)
            )

    return IngestionResult(
        table=contract.name,
        rows=len(rows),
        path=str(target),
        strategy=contract.strategy.value,
        cursor_value=new_watermark,
    )


def ingest_all(
    engine, *, full_refresh: bool = False, ingest_date: date | None = None
) -> list[IngestionResult]:
    """Execute l'extraction complete de la couche bronze."""
    ingest_date = ingest_date or date.today()
    batch_id = f"bronze_{datetime.now(UTC):%Y%m%dT%H%M%S}"
    watermarks = {} if full_refresh else read_watermarks()
    results: list[IngestionResult] = []

    with engine.connect() as connection:
        for contract in CONTRACTS:
            result = extract_table(
                connection,
                contract,
                ingest_date,
                batch_id,
                watermark=watermarks.get(contract.name),
            )
            results.append(result)
            if result.cursor_value:
                watermarks[contract.name] = result.cursor_value

    write_watermarks(watermarks)
    return results


def register_bronze_views(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Declare une vue DuckDB par table bronze, sur l'ensemble des partitions.

    L'equivalent cloud est une table externe Fabric / Synapse pointant sur le
    meme chemin ADLS : le SQL des couches superieures reste identique.
    """
    registered: list[str] = []
    root = settings.layer_path("bronze")

    for contract in CONTRACTS:
        table_dir = root / contract.name
        if not table_dir.exists() or not any(table_dir.rglob("*.parquet")):
            # Table jamais ingeree : on cree une table vide mais typee, pour que
            # le SQL des couches silver et gold s'execute malgre tout.
            con.execute(
                f"CREATE OR REPLACE TABLE bronze_{contract.name} "
                f"({contract.duckdb_schema()}, _ingested_at TIMESTAMP, _batch_id VARCHAR, "
                "_source_system VARCHAR)"
            )
            registered.append(f"bronze_{contract.name} (vide)")
            continue

        pattern = (table_dir / "**" / "*.parquet").as_posix()
        con.execute(
            f"CREATE OR REPLACE VIEW bronze_{contract.name} AS "
            f"SELECT * FROM read_parquet('{pattern}', union_by_name=true, hive_partitioning=true)"
        )
        registered.append(f"bronze_{contract.name}")

    return registered
