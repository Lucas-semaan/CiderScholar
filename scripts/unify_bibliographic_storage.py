"""Move selected legacy bibliographic runs into the authoritative scientific corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.config import load_settings
from app.database.sqlite import Database
from app.services.bibliographic_unification import merge_bibliographic_runs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--run-id", action="append", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create a verified SQLite backup, then apply the additive merge.",
    )
    return parser


def _backup_database(source: Path, destination: Path) -> tuple[str, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(source)) as origin,
        closing(sqlite3.connect(destination)) as target,
    ):
        origin.backup(target)
    with closing(
        sqlite3.connect(f"file:{destination.resolve().as_posix()}?mode=ro", uri=True)
    ) as verified:
        if verified.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("bibliographic unification backup failed quick_check")
    digest = hashlib.sha256()
    with destination.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest(), destination.stat().st_size


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    settings = load_settings(arguments.config)
    source = settings.paths.database_path.resolve()
    target = settings.paths.scientific_database_path.resolve()
    if source == target:
        raise ValueError("legacy and authoritative scientific databases are already identical")
    if not arguments.apply:
        print(
            json.dumps(
                {
                    "state": "dry_run",
                    "source_database": str(source),
                    "target_database": str(target),
                    "run_ids": list(dict.fromkeys(arguments.run_id)),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = settings.paths.exports_dir / "bibliographic-unification" / stamp
    backup = settings.paths.data_dir / "backups" / "sqlite" / f"pre-unification-{stamp}.sqlite3"
    backup_sha256, backup_size = _backup_database(target, backup)
    report = merge_bibliographic_runs(
        Database(source),
        Database(target),
        run_ids=arguments.run_id,
    )
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "state": "applied",
        "backup": {
            "path": str(backup),
            "sha256": backup_sha256,
            "size_bytes": backup_size,
        },
        "report": report.model_dump(mode="json"),
    }
    root.mkdir(parents=True, exist_ok=True)
    report_path = root / "report.json"
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"report": str(report_path), **payload}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
