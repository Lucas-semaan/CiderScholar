from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

import pytest

from app.database.sqlite import Database
from scripts.finalize_corpus_expansion import (
    _require_no_running_harvest,
    _verify_backup,
    build_parser,
)


def test_finalizer_requires_explicit_apply_flag() -> None:
    arguments = build_parser().parse_args(
        ["--apply", "--backup-manifest", "backup.manifest.json", "--no-index"]
    )

    assert arguments.apply is True
    assert arguments.no_index is True


def test_backup_verification_checks_hash_size_and_sqlite(tmp_path) -> None:
    backup = tmp_path / "backup.sqlite3"
    with sqlite3.connect(backup) as connection:
        connection.execute("CREATE TABLE proof (value TEXT NOT NULL)")
        connection.execute("INSERT INTO proof VALUES ('ok')")
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    manifest = tmp_path / "backup.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "backup": str(backup),
                "size": backup.stat().st_size,
                "sha256": digest,
                "sqlite_quick_check": "ok",
            }
        ),
        encoding="utf-8",
    )

    verified = _verify_backup(manifest)

    assert verified["sha256"] == digest
    assert verified["sqlite_quick_check"] == "ok"


def test_finalizer_refuses_a_running_harvest(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO bibliographic_harvest_runs (
                id, profile, state, themes, sources, per_source_limit,
                request_delay_seconds, started_at
            ) VALUES ('running-test', 'running_profile', 'running', '{}', '[]', 1, 1, ?)
            """,
            (datetime.now(UTC).isoformat(),),
        )

    with pytest.raises(RuntimeError, match="running_profile"):
        _require_no_running_harvest(database)
