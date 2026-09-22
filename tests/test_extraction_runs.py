from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from app.database.migrations import CURRENT_SCHEMA_VERSION
from app.database.sqlite import Database


def _start_run(
    database: Database,
    *,
    run_id: str = "extract-run-1",
    article_id: str | None = None,
    file_sha256: str = "a" * 64,
) -> dict[str, object]:
    return database.start_extraction_run(
        run_id=run_id,
        file_sha256=file_sha256,
        article_id=article_id,
        parser_id="pymupdf",
        parser_version="1.26.0",
        contract_version="1.0.0",
        config_sha256="b" * 64,
    )


def _save_article(database: Database) -> None:
    database.save_article_and_chunks(
        {
            "id": "article-1",
            "sha256": "c" * 64,
            "title": "Extraction run test article",
            "pdf_path": "data/pdf/extraction-run-test.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [],
    )


def test_fresh_database_creates_extraction_runs_with_expected_contract(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()

    with closing(database.connect()) as connection:
        columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(extraction_runs)")
        }
        foreign_keys = {
            (str(row["table"]), str(row["from"]), str(row["on_delete"]))
            for row in connection.execute("PRAGMA foreign_key_list(extraction_runs)")
        }
        version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]

    assert columns == {
        "id",
        "file_sha256",
        "article_id",
        "parser_id",
        "parser_version",
        "contract_version",
        "config_sha256",
        "model_name",
        "model_sha256",
        "state",
        "page_count",
        "element_count",
        "warning_count",
        "normalized_text_sha256",
        "duration_seconds",
        "error_type",
        "error_message",
        "started_at",
        "updated_at",
        "completed_at",
    }
    assert ("articles", "article_id", "SET NULL") in foreign_keys
    assert version == CURRENT_SCHEMA_VERSION


def test_migration_from_previous_version_adds_extraction_runs(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    with closing(database.connect()) as connection, connection:
        connection.execute("DROP INDEX idx_extraction_runs_article")
        connection.execute("DROP INDEX idx_extraction_runs_identity")
        connection.execute("DROP TABLE extraction_runs")
        connection.execute("DELETE FROM schema_version WHERE version >= ?", (38,))

    database.initialize()
    with closing(database.connect()) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'extraction_runs'"
        ).fetchone()


def test_extraction_run_sql_constraints_reject_invalid_identity_and_state(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()

    with closing(database.connect()) as connection, connection:
        values = (
            "run-raw",
            "a" * 64,
            "pymupdf",
            "1.0",
            "1.0.0",
            "b" * 64,
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO extraction_runs(
                    id, file_sha256, parser_id, parser_version, contract_version,
                    config_sha256, state
                ) VALUES (?, ?, ?, ?, ?, ?, 'unknown')
                """,
                values,
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO extraction_runs(
                    id, file_sha256, parser_id, parser_version, contract_version,
                    config_sha256, model_name, state
                ) VALUES (?, ?, ?, ?, ?, ?, 'layout-model', 'started')
                """,
                values,
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO extraction_runs(
                    id, file_sha256, parser_id, parser_version, contract_version,
                    config_sha256, state
                ) VALUES (?, ?, ?, ?, ?, ?, 'started')
                """,
                ("run-invalid-hash", "A" * 64, *values[2:]),
            )


def test_extraction_run_start_is_idempotent_for_the_same_identity(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()

    first = _start_run(database, run_id="extract-run-first")
    resumed = _start_run(database, run_id="extract-run-second")

    assert first["id"] == "extract-run-first"
    assert resumed["id"] == "extract-run-first"
    assert resumed["state"] == "started"
    with pytest.raises(ValueError, match="another identity"):
        database.start_extraction_run(
            run_id="extract-run-first",
            file_sha256="d" * 64,
            article_id=None,
            parser_id="pymupdf",
            parser_version="1.26.0",
            contract_version="1.0.0",
            config_sha256="b" * 64,
        )


def test_extraction_run_resume_attaches_only_a_missing_article_atomically(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _save_article(database)

    _start_run(database, article_id=None)
    attached = _start_run(database, run_id="ignored-on-resume", article_id="article-1")
    repeated = _start_run(database, article_id="article-1")

    assert attached["id"] == "extract-run-1"
    assert attached["article_id"] == "article-1"
    assert repeated["article_id"] == "article-1"
    with pytest.raises(ValueError, match="another article"):
        _start_run(database, article_id="different-article")
    with pytest.raises(sqlite3.IntegrityError):
        _start_run(
            database,
            run_id="extract-run-missing",
            article_id="missing-article",
            file_sha256="e" * 64,
        )


def test_extraction_run_terminal_transitions_are_atomic_and_closed(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _start_run(database)

    database.complete_extraction_run(
        run_id="extract-run-1",
        page_count=4,
        element_count=2,
        warning_count=1,
        normalized_text_sha256="d" * 64,
        duration_seconds=1.25,
    )
    completed = database.extraction_run("extract-run-1")

    assert completed is not None
    assert completed["state"] == "completed"
    assert completed["completed_at"] is not None
    assert completed["page_count"] == 4
    assert completed["normalized_text_sha256"] == "d" * 64
    database.complete_extraction_run(
        run_id="extract-run-1",
        page_count=4,
        element_count=2,
        warning_count=1,
        normalized_text_sha256="d" * 64,
        duration_seconds=1.25,
    )
    with pytest.raises(ValueError, match="already terminal"):
        database.mark_extraction_run_review_required(
            run_id="extract-run-1",
            page_count=4,
            element_count=2,
            warning_count=1,
        )
    with pytest.raises(ValueError, match="different payload"):
        database.complete_extraction_run(
            run_id="extract-run-1",
            page_count=5,
            element_count=2,
            warning_count=1,
            normalized_text_sha256="d" * 64,
            duration_seconds=1.25,
        )

    _start_run(
        database,
        run_id="extract-run-review",
        article_id=None,
        file_sha256="e" * 64,
    )
    database.mark_extraction_run_review_required(
        run_id="extract-run-review",
        page_count=2,
        element_count=0,
        warning_count=3,
    )
    assert database.extraction_run("extract-run-review")["state"] == "review_required"
    database.mark_extraction_run_review_required(
        run_id="extract-run-review",
        page_count=2,
        element_count=0,
        warning_count=3,
    )


def test_extraction_run_failure_diagnostics_are_bounded_and_safe(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _start_run(database)

    with pytest.raises(ValueError, match="control character"):
        database.fail_extraction_run(
            run_id="extract-run-1",
            error_type="parser_failure",
            error_message="untrusted parser output\nsource text must not be persisted",
        )
    with pytest.raises(ValueError, match="invalid"):
        database.fail_extraction_run(
            run_id="extract-run-1",
            error_type="parser_failure",
            error_message="x" * 241,
        )

    database.fail_extraction_run(
        run_id="extract-run-1",
        error_type="parser_failure",
        error_message="parser returned invalid contract",
        duration_seconds=0.5,
    )
    failed = database.extraction_run("extract-run-1")
    assert failed is not None
    assert failed["state"] == "failed"
    assert failed["error_type"] == "parser_failure"
    assert failed["error_message"] == "parser returned invalid contract"
    assert failed["completed_at"] is not None
    database.fail_extraction_run(
        run_id="extract-run-1",
        error_type="parser_failure",
        error_message="parser returned invalid contract",
        duration_seconds=0.5,
    )
    with pytest.raises(ValueError, match="different payload"):
        database.fail_extraction_run(
            run_id="extract-run-1",
            error_type="parser_failure",
            error_message="different technical diagnostic",
            duration_seconds=0.5,
        )


@pytest.mark.parametrize(
    "duration_seconds", [True, float("nan"), float("inf"), -float("inf"), 86400.1]
)
def test_extraction_run_rejects_invalid_or_excessive_durations(
    settings,
    duration_seconds: float,
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _start_run(database)

    with pytest.raises(ValueError, match="duration seconds"):
        database.complete_extraction_run(
            run_id="extract-run-1",
            page_count=1,
            element_count=0,
            warning_count=0,
            duration_seconds=duration_seconds,
        )


def test_extraction_run_sql_duration_ceiling_is_enforced(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()

    with (
        closing(database.connect()) as connection,
        connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.execute(
            """
            INSERT INTO extraction_runs(
                id, file_sha256, parser_id, parser_version, contract_version,
                config_sha256, state, duration_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, 'started', ?)
            """,
            ("run-duration", "a" * 64, "pymupdf", "1.0", "1.0.0", "b" * 64, 86400.1),
        )


def test_article_deletion_preserves_extraction_run_and_nulls_article(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _save_article(database)
    _start_run(database, article_id="article-1")

    database.delete_article("article-1")
    run = database.extraction_run("extract-run-1")

    assert run is not None
    assert run["article_id"] is None
