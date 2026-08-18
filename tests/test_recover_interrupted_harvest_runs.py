from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.database.sqlite import Database
from scripts.recover_interrupted_harvest_runs import (
    _require_exact_running_runs,
    build_parser,
)


def _insert_running(database: Database, run_id: str, profile: str) -> None:
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO bibliographic_harvest_runs (
                id, profile, state, themes, sources, per_source_limit,
                request_delay_seconds, started_at
            ) VALUES (?, ?, 'running', '{}', '[]', 1, 1, ?)
            """,
            (run_id, profile, datetime.now(UTC).isoformat()),
        )


def test_recovery_parser_requires_explicit_apply_and_reason() -> None:
    arguments = build_parser().parse_args(
        [
            "--run-id",
            "interrupted-1",
            "--reason",
            "verified interrupted process",
            "--apply",
        ]
    )

    assert arguments.run_id == ["interrupted-1"]
    assert arguments.reason == "verified interrupted process"
    assert arguments.apply is True


def test_recovery_requires_exactly_all_running_rows(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _insert_running(database, "interrupted-1", "profile-1")
    _insert_running(database, "still-active-2", "profile-2")

    with pytest.raises(RuntimeError, match="unselected_running=.*still-active-2"):
        _require_exact_running_runs(database, ("interrupted-1",))

    rows = _require_exact_running_runs(
        database,
        ("interrupted-1", "still-active-2"),
    )

    assert {row["id"] for row in rows} == {"interrupted-1", "still-active-2"}
