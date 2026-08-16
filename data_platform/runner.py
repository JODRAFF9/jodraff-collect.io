"""Orchestrateur du pipeline medallion.

Execute la chaine complete bronze -> silver -> gold, puis exporte la couche
gold en Parquet pour la consommation par Power BI, Fabric ou la generation des
rapports LaTeX.

En local, tout tourne dans DuckDB. Dans Azure, ce meme SQL est execute par
Fabric ou Synapse sur les memes fichiers Parquet stockes dans ADLS Gen2 :
seule la connexion change, pas la logique.

Usage :
    python -m data_platform.runner run            # pipeline complet
    python -m data_platform.runner bronze         # extraction seule
    python -m data_platform.runner transform      # silver + gold seuls
    python -m data_platform.runner quality        # controles qualite
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from data_platform import bronze
from data_platform.contracts import CONTRACTS
from platform_core.config import settings

SQL_DIR = Path(__file__).resolve().parent / "sql"

GOLD_EXPORTS = (
    "dim_date",
    "dim_survey",
    "dim_collection_service",
    "dim_enumerator",
    "dim_question",
    "dim_geography",
    "dim_interview_status",
    "fact_interview",
    "fact_answer",
    "fact_fieldwork_daily",
    "fact_sample_coverage",
    "fact_quota",
)


@dataclass
class StepResult:
    name: str
    status: str
    duration_ms: int
    detail: dict = field(default_factory=dict)


@dataclass
class RunReport:
    started_at: str
    steps: list[StepResult] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(s.status == "failed" for s in self.steps)

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "success": not self.failed,
            "steps": [
                {
                    "name": s.name,
                    "status": s.status,
                    "duration_ms": s.duration_ms,
                    "detail": s.detail,
                }
                for s in self.steps
            ],
        }


def connect_warehouse(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Ouvre l'entrepot analytique local."""
    Path(settings.warehouse_url).parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(settings.warehouse_url, read_only=read_only)


def _sql_files(layer: str) -> list[Path]:
    """Fichiers SQL d'une couche, dans l'ordre de leur prefixe numerique."""
    directory = SQL_DIR / layer
    if not directory.exists():
        return []
    return sorted(directory.glob("*.sql"))


def _split_statements(sql: str) -> list[str]:
    """Decoupe un fichier SQL en instructions executables.

    Un simple ``split(';')`` ne suffit pas : un point-virgule peut apparaitre
    dans un commentaire ou dans une chaine litterale. On parcourt donc le texte
    en suivant l'etat courant (chaine ouverte, commentaire de ligne, commentaire
    de bloc) et on ne coupe qu'aux points-virgules reellement significatifs.
    """
    statements: list[str] = []
    buffer: list[str] = []
    in_string = in_line_comment = in_block_comment = False
    index = 0

    while index < len(sql):
        char = sql[index]
        pair = sql[index : index + 2]

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                buffer.append(char)
            index += 1
            continue

        if in_block_comment:
            if pair == "*/":
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue

        if in_string:
            buffer.append(char)
            if char == "'":
                # Une apostrophe doublee est un echappement, pas une fermeture.
                if sql[index + 1 : index + 2] == "'":
                    buffer.append("'")
                    index += 2
                    continue
                in_string = False
            index += 1
            continue

        if pair == "--":
            in_line_comment = True
            index += 2
            continue
        if pair == "/*":
            in_block_comment = True
            index += 2
            continue
        if char == "'":
            in_string = True
            buffer.append(char)
            index += 1
            continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue

        buffer.append(char)
        index += 1

    trailing = "".join(buffer).strip()
    if trailing:
        statements.append(trailing)
    return statements


# --------------------------------------------------------------------------
# Etapes
# --------------------------------------------------------------------------


def run_bronze(full_refresh: bool = False) -> StepResult:
    """Extrait la base operationnelle vers la couche bronze."""
    from apps.api.db import engine

    start = time.perf_counter()
    try:
        results = bronze.ingest_all(engine, full_refresh=full_refresh)
        total = sum(r.rows for r in results)
        return StepResult(
            name="bronze",
            status="success",
            duration_ms=int((time.perf_counter() - start) * 1000),
            detail={
                "tables": len(results),
                "rows_ingested": total,
                "per_table": {r.table: r.rows for r in results},
            },
        )
    except Exception as exc:  # noqa: BLE001 - le rapport doit refleter l'echec
        return StepResult(
            name="bronze",
            status="failed",
            duration_ms=int((time.perf_counter() - start) * 1000),
            detail={"error": str(exc)},
        )


def run_layer(con: duckdb.DuckDBPyConnection, layer: str) -> StepResult:
    """Execute tous les modeles SQL d'une couche."""
    start = time.perf_counter()
    executed: list[str] = []
    try:
        for path in _sql_files(layer):
            for statement in _split_statements(path.read_text(encoding="utf-8")):
                con.execute(statement)
            executed.append(path.name)
        return StepResult(
            name=layer,
            status="success",
            duration_ms=int((time.perf_counter() - start) * 1000),
            detail={"models": executed},
        )
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            name=layer,
            status="failed",
            duration_ms=int((time.perf_counter() - start) * 1000),
            detail={"error": str(exc), "executed": executed},
        )


def export_gold(con: duckdb.DuckDBPyConnection) -> StepResult:
    """Exporte la couche gold en Parquet.

    C'est ce repertoire que Power BI interroge (ou auquel Fabric attache un
    raccourci OneLake) et que lit le generateur de rapports LaTeX.
    """
    start = time.perf_counter()
    gold_dir = settings.layer_path("gold")
    gold_dir.mkdir(parents=True, exist_ok=True)
    exported: dict[str, int] = {}

    try:
        for table in GOLD_EXPORTS:
            exists = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table]
            ).fetchone()[0]
            if not exists:
                continue
            target = gold_dir / f"{table}.parquet"
            con.execute(
                f"COPY {table} TO '{target.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            exported[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        return StepResult(
            name="export_gold",
            status="success",
            duration_ms=int((time.perf_counter() - start) * 1000),
            detail={"tables": exported, "path": str(gold_dir)},
        )
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            name="export_gold",
            status="failed",
            duration_ms=int((time.perf_counter() - start) * 1000),
            detail={"error": str(exc)},
        )


def run_pipeline(full_refresh: bool = False, skip_bronze: bool = False) -> RunReport:
    """Chaine complete : bronze, silver, gold, export, controles."""
    from data_platform.quality import run_checks

    report = RunReport(started_at=datetime.now(UTC).isoformat())

    if not skip_bronze:
        step = run_bronze(full_refresh=full_refresh)
        report.steps.append(step)
        if step.status == "failed":
            return report

    con = connect_warehouse()
    try:
        start = time.perf_counter()
        registered = bronze.register_bronze_views(con)
        report.steps.append(
            StepResult(
                name="register_bronze",
                status="success",
                duration_ms=int((time.perf_counter() - start) * 1000),
                detail={"views": len(registered)},
            )
        )

        for layer in ("silver", "gold"):
            step = run_layer(con, layer)
            report.steps.append(step)
            if step.status == "failed":
                return report

        # Les controles precedent l'export : une couche gold incoherente ne
        # doit jamais atteindre Power BI ni les rapports. En cas d'echec
        # bloquant, l'export precedent reste en place, et les consommateurs
        # continuent d'afficher le dernier etat valide.
        start = time.perf_counter()
        checks = run_checks(con)
        failures = [c for c in checks if not c["passed"] and c["severity"] == "error"]
        report.steps.append(
            StepResult(
                name="quality",
                status="failed" if failures else "success",
                duration_ms=int((time.perf_counter() - start) * 1000),
                detail={"checks": checks, "failures": len(failures)},
            )
        )
        if failures:
            report.steps.append(
                StepResult(
                    name="export_gold",
                    status="skipped",
                    duration_ms=0,
                    detail={
                        "reason": "Export annule : "
                        f"{len(failures)} controle(s) bloquant(s) en echec.",
                        "failed_checks": [c["name"] for c in failures],
                    },
                )
            )
            return report

        report.steps.append(export_gold(con))
    finally:
        con.close()

    return report


def write_report(report: RunReport) -> Path:
    """Persiste le rapport d'execution, pour l'observabilite du pipeline."""
    log_dir = Path(settings.lake_root).parent / "pipeline_runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"run_{datetime.now(UTC):%Y%m%dT%H%M%S}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Interface en ligne de commande
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline medallion Jodraff Collect")
    parser.add_argument(
        "command",
        choices=["run", "bronze", "transform", "quality", "contracts"],
        help="Etape a executer",
    )
    parser.add_argument("--full-refresh", action="store_true", help="Ignorer les watermarks")
    parser.add_argument("--json", action="store_true", help="Sortie JSON brute")
    args = parser.parse_args(argv)

    if args.command == "contracts":
        payload = [
            {
                "table": c.name,
                "source": c.source_table,
                "strategy": c.strategy.value,
                "columns": len(c.columns),
                "pii_columns": c.pii_columns,
            }
            for c in CONTRACTS
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if args.command == "bronze":
        report = RunReport(started_at=datetime.now(UTC).isoformat())
        report.steps.append(run_bronze(full_refresh=args.full_refresh))
    elif args.command == "transform":
        report = run_pipeline(skip_bronze=True)
    elif args.command == "quality":
        from data_platform.quality import run_checks

        con = connect_warehouse(read_only=True)
        try:
            checks = run_checks(con)
        finally:
            con.close()
        print(json.dumps(checks, indent=2, ensure_ascii=False))
        return 0 if all(c["passed"] or c["severity"] != "error" for c in checks) else 1
    else:
        report = run_pipeline(full_refresh=args.full_refresh)

    path = write_report(report)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"Pipeline {'OK' if not report.failed else 'EN ECHEC'} — rapport : {path}")
        for step in report.steps:
            marker = "OK  " if step.status == "success" else "ECHEC"
            print(f"  [{marker}] {step.name:18} {step.duration_ms:>6} ms")
            if step.status == "failed":
                print(f"          {step.detail.get('error')}")

    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
