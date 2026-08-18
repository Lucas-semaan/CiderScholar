"""Close explicitly identified harvest runs left active by an interrupted process."""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.updates.harvest import BibliographicHarvestStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--run-id",
        action="append",
        required=True,
        help="Exact interrupted run id; repeat for every remaining running row",
    )
    parser.add_argument(
        "--reason",
        required=True,
        help="Auditable reason explaining why the selected processes were interrupted",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Confirm mutation of the explicitly selected running rows",
    )
    parser.add_argument("--report-path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if not arguments.apply:
        raise SystemExit("--apply is required to recover interrupted harvest runs")
    run_ids = tuple(dict.fromkeys(str(value).strip() for value in arguments.run_id))
    if not all(run_ids):
        raise SystemExit("--run-id values must not be empty")

    settings = settings_for_corpus(load_settings(arguments.config), CorpusScope.COMMON)
    settings.paths.create()
    database = Database(settings.paths.database_path)
    database.initialize()
    running = _require_exact_running_runs(database, run_ids)
    store = BibliographicHarvestStore(database)
    completed_at = datetime.now(UTC)
    recovered = [
        store.recover_interrupted_run(
            run_id=run_id,
            reason=arguments.reason,
            completed_at=completed_at,
        )
        for run_id in run_ids
    ]
    payload = {
        "generated_at": completed_at.isoformat(),
        "database_path": str(database.path.resolve()),
        "selected_running_runs": running,
        "recovered": recovered,
    }
    report_path = arguments.report_path or (
        settings.paths.exports_dir
        / f"interrupted-harvest-recovery-{completed_at:%Y%m%dT%H%M%SZ}.json"
    )
    _write_json(report_path.resolve(), payload)
    print(json.dumps({**payload, "report_path": str(report_path.resolve())}, ensure_ascii=False))
    return 0


def _require_exact_running_runs(
    database: Database,
    selected_run_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    with closing(database.connect()) as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, profile, started_at
                FROM bibliographic_harvest_runs
                WHERE state = 'running'
                ORDER BY started_at, id
                """
            )
        ]
    running_ids = {str(row["id"]) for row in rows}
    selected_ids = set(selected_run_ids)
    if running_ids != selected_ids:
        missing = sorted(running_ids - selected_ids)
        inactive = sorted(selected_ids - running_ids)
        raise RuntimeError(
            "selected run ids must exactly match all running harvest rows; "
            f"unselected_running={missing}; selected_not_running={inactive}"
        )
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
