from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.database.chat_progress_migration import add_chat_progress_steps
from app.database.corpus_ingestion_job_migration import rename_private_ingestion_job_contract
from app.database.migrations import CURRENT_SCHEMA_VERSION, MIGRATIONS
from app.database.sqlite import Database


def _seed_chat(connection: sqlite3.Connection) -> tuple[str, str]:
    conversation_id = str(uuid4())
    message_id = str(uuid4())
    connection.execute(
        "INSERT INTO chat_conversations(id, title) VALUES (?, 'Test')", (conversation_id,)
    )
    connection.execute(
        """
        INSERT INTO chat_messages(id, conversation_id, position, role, content)
        VALUES (?, ?, 0, 'user', 'Question')
        """,
        (message_id, conversation_id),
    )
    return conversation_id, message_id


def _insert_job(
    connection: sqlite3.Connection,
    *,
    conversation_id: str,
    message_id: str,
    job_type: str = "chat_answer",
    state: str = "queued",
    priority: int = 100,
    attempt: int = 0,
    available_at: str | None = None,
    worker_id: str | None = None,
    lease_expires_at: str | None = None,
    heartbeat_at: str | None = None,
    client_request_id: str | None = None,
) -> str:
    job_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    connection.execute(
        """
        INSERT INTO jobs(
            id, type, state, step, payload_json, priority, attempt, available_at,
            worker_id, lease_expires_at, heartbeat_at,
            conversation_id, user_message_id, client_request_id,
            created_at, updated_at
        ) VALUES (
            :id, :type, :state, 'waiting', :payload_json, :priority, :attempt, :available_at,
            :worker_id, :lease_expires_at, :heartbeat_at,
            :conversation_id, :message_id, :client_request_id,
            :now, :now
        )
        """,
        {
            "id": job_id,
            "type": job_type,
            "state": state,
            "payload_json": json.dumps({"version": 1, "message": "Question"}),
            "priority": priority,
            "attempt": attempt,
            "available_at": available_at or now,
            "worker_id": worker_id,
            "lease_expires_at": lease_expires_at,
            "heartbeat_at": heartbeat_at,
            "now": now,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "client_request_id": client_request_id or str(uuid4()),
        },
    )
    return job_id


def test_jobs_use_a_new_append_only_migration(settings) -> None:
    assert max(MIGRATIONS) == CURRENT_SCHEMA_VERSION
    assert 13 in MIGRATIONS
    assert 14 in MIGRATIONS
    assert 27 in MIGRATIONS
    assert 29 in MIGRATIONS
    assert set(range(2, CURRENT_SCHEMA_VERSION + 1)) == set(MIGRATIONS)

    database = Database(settings.paths.database_path)
    database.initialize()
    with closing(database.connect()) as connection:
        version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]

    assert version == CURRENT_SCHEMA_VERSION


def test_jobs_reject_unknown_type_and_state_in_sqlite(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    with closing(database.connect()) as connection, connection:
        conversation_id, message_id = _seed_chat(connection)
        job_id = _insert_job(connection, conversation_id=conversation_id, message_id=message_id)
        assert connection.execute("SELECT type, state FROM jobs WHERE id = ?", (job_id,)).fetchone()

        with pytest.raises(sqlite3.IntegrityError):
            _insert_job(
                connection,
                conversation_id=conversation_id,
                message_id=message_id,
                job_type="unknown",
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_job(
                connection,
                conversation_id=conversation_id,
                message_id=message_id,
                state="unknown",
            )


def test_chat_progress_migration_preserves_legacy_argo_jobs(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()

    with closing(database.connect()) as connection, connection:
        # Recreate the v28 closed step contract, then prove the v29 rebuild is lossless.
        rename_private_ingestion_job_contract(connection)
        conversation_id, message_id = _seed_chat(connection)
        job_id = _insert_job(
            connection,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        connection.execute("UPDATE jobs SET step = 'argo' WHERE id = ?", (job_id,))
        connection.execute(
            """
            INSERT INTO job_events(job_id, state, step, technical_message, created_at)
            VALUES (?, 'queued', 'argo', 'job.step.argo', ?)
            """,
            (job_id, datetime.now(UTC).isoformat()),
        )

        add_chat_progress_steps(connection)

        assert (
            connection.execute("SELECT step FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
            == "argo"
        )
        assert (
            connection.execute(
                "SELECT step FROM job_events WHERE job_id = ?", (job_id,)
            ).fetchone()[0]
            == "argo"
        )
        jobs_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()[0]

    for step in (
        "planning",
        "evidence_selection",
        "coverage",
        "figure_analysis",
        "generation",
    ):
        assert f"'{step}'" in jobs_sql


def test_corpus_ingestion_migration_preserves_existing_private_jobs(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()

    with closing(database.connect()) as connection, connection:
        jobs_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()[0]
        job_events_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'job_events'"
        ).fetchone()[0]
        connection.executescript(
            """
            DROP INDEX idx_jobs_claim;
            DROP INDEX idx_jobs_conversation;
            DROP INDEX idx_job_events_job;
            DROP INDEX idx_single_active_weekly_maintenance;
            ALTER TABLE job_events RENAME TO job_events_current;
            ALTER TABLE jobs RENAME TO jobs_current;
            """
        )
        connection.execute(jobs_sql.replace("'corpus_ingestion'", "'private_ingestion'"))
        connection.execute(job_events_sql)
        connection.execute("INSERT INTO jobs SELECT * FROM jobs_current")
        connection.execute("INSERT INTO job_events SELECT * FROM job_events_current")
        connection.executescript("DROP TABLE job_events_current; DROP TABLE jobs_current;")
        conversation_id, message_id = _seed_chat(connection)
        job_id = _insert_job(
            connection,
            conversation_id=conversation_id,
            message_id=message_id,
            job_type="private_ingestion",
        )

        rename_private_ingestion_job_contract(connection)

        migrated_type = connection.execute(
            "SELECT type FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()[0]
        jobs_constraint = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()[0]

    assert migrated_type == "corpus_ingestion"
    assert "'private_ingestion'" not in jobs_constraint
    assert "'corpus_ingestion'" in jobs_constraint


def test_job_can_be_prioritized_attempted_and_deferred(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    deferred_until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    with closing(database.connect()) as connection, connection:
        conversation_id, message_id = _seed_chat(connection)
        job_id = _insert_job(
            connection,
            conversation_id=conversation_id,
            message_id=message_id,
            priority=20,
            attempt=2,
            available_at=deferred_until,
        )
        row = connection.execute(
            """
            SELECT priority, attempt, available_at, created_at, updated_at,
                   started_at, completed_at
            FROM jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()

    assert row["priority"] == 20
    assert row["attempt"] == 2
    assert row["available_at"] == deferred_until
    assert row["created_at"]
    assert row["updated_at"]
    assert row["started_at"] is None
    assert row["completed_at"] is None


def test_running_job_lease_and_heartbeat_make_abandonment_detectable(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    now = datetime.now(UTC)
    expired_at = (now - timedelta(seconds=1)).isoformat()
    heartbeat_at = (now - timedelta(seconds=30)).isoformat()

    with closing(database.connect()) as connection, connection:
        conversation_id, message_id = _seed_chat(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_job(
                connection,
                conversation_id=conversation_id,
                message_id=message_id,
                state="running",
            )

        job_id = _insert_job(
            connection,
            conversation_id=conversation_id,
            message_id=message_id,
            state="running",
            worker_id="worker-test",
            lease_expires_at=expired_at,
            heartbeat_at=heartbeat_at,
        )
        abandoned = connection.execute(
            """
            SELECT id, worker_id, lease_expires_at, heartbeat_at
            FROM jobs
            WHERE state = 'running' AND lease_expires_at < ?
            """,
            (now.isoformat(),),
        ).fetchone()

    assert abandoned["id"] == job_id
    assert abandoned["worker_id"] == "worker-test"
    assert abandoned["lease_expires_at"] == expired_at
    assert abandoned["heartbeat_at"] == heartbeat_at


def test_chat_job_requires_persisted_conversation_and_user_message(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()

    with closing(database.connect()) as connection, connection:
        conversation_id, message_id = _seed_chat(connection)
        job_id = _insert_job(connection, conversation_id=conversation_id, message_id=message_id)
        linked = connection.execute(
            "SELECT conversation_id, user_message_id FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        assert tuple(linked) == (conversation_id, message_id)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_job(
                connection,
                conversation_id=str(uuid4()),
                message_id=message_id,
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_job(
                connection,
                conversation_id=conversation_id,
                message_id=str(uuid4()),
            )

        connection.execute("DELETE FROM chat_messages WHERE id = ?", (message_id,))
        assert connection.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone() is None


def test_job_idempotence_is_scoped_by_conversation(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    request_id = str(uuid4())

    with closing(database.connect()) as connection, connection:
        conversation_id, message_id = _seed_chat(connection)
        first_id = _insert_job(
            connection,
            conversation_id=conversation_id,
            message_id=message_id,
            client_request_id=request_id,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_job(
                connection,
                conversation_id=conversation_id,
                message_id=message_id,
                client_request_id=request_id,
            )

        rows = connection.execute(
            """
            SELECT id FROM jobs
            WHERE conversation_id = ? AND client_request_id = ?
            """,
            (conversation_id, request_id),
        ).fetchall()

    assert [row["id"] for row in rows] == [first_id]


def test_job_events_store_only_bounded_technical_progress(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    now = datetime.now(UTC).isoformat()

    with closing(database.connect()) as connection, connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(job_events)")}
        conversation_id, message_id = _seed_chat(connection)
        job_id = _insert_job(connection, conversation_id=conversation_id, message_id=message_id)
        connection.execute(
            """
            INSERT INTO job_events(job_id, state, step, technical_message, created_at)
            VALUES (?, 'queued', 'waiting', 'job.enqueued', ?)
            """,
            (job_id, now),
        )
        event = connection.execute(
            """
            SELECT job_id, state, step, technical_message, created_at
            FROM job_events WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO job_events(job_id, state, step, technical_message, created_at)
                VALUES (?, 'unknown', 'waiting', 'job.invalid', ?)
                """,
                (job_id, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO job_events(job_id, state, step, technical_message, created_at)
                VALUES (?, 'queued', 'waiting', ?, ?)
                """,
                (job_id, "x" * 301, now),
            )

    assert columns == {"id", "job_id", "state", "step", "technical_message", "created_at"}
    assert not {"payload_json", "content", "response_json"} & columns
    assert tuple(event) == (job_id, "queued", "waiting", "job.enqueued", now)


def test_job_claim_and_conversation_queries_use_targeted_indexes(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    now = datetime.now(UTC).isoformat()

    with closing(database.connect()) as connection:
        claim_plan = " ".join(
            row["detail"]
            for row in connection.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT id FROM jobs
                WHERE state = 'queued' AND available_at <= ?
                ORDER BY priority, available_at, created_at
                LIMIT 1
                """,
                (now,),
            )
        )
        conversation_plan = " ".join(
            row["detail"]
            for row in connection.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT id FROM jobs
                WHERE conversation_id = ? AND state IN ('queued', 'running', 'cancel_requested')
                ORDER BY created_at DESC
                """,
                (str(uuid4()),),
            )
        )

    assert "idx_jobs_claim" in claim_plan
    assert "idx_jobs_conversation" in conversation_plan


def test_migration_from_version_11_preserves_existing_conversations(settings) -> None:
    database = Database(settings.paths.database_path)
    schema_sql = (Path(__file__).parents[1] / "app" / "database" / "schema.sql").read_text(
        encoding="utf-8"
    )
    conversation_id = str(uuid4())
    message_id = str(uuid4())

    settings.paths.database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.paths.database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema_sql)
        for version in range(2, 12):
            connection.executescript(MIGRATIONS[version])
            connection.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
        connection.execute(
            "INSERT INTO chat_conversations(id, title) VALUES (?, 'Conversation conservée')",
            (conversation_id,),
        )
        connection.execute(
            """
            INSERT INTO chat_messages(id, conversation_id, position, role, content)
            VALUES (?, ?, 0, 'user', 'Message conservé')
            """,
            (message_id, conversation_id),
        )

    database.initialize()
    with closing(database.connect()) as connection:
        conversation = connection.execute(
            "SELECT title FROM chat_conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        message = connection.execute(
            "SELECT content FROM chat_messages WHERE id = ?", (message_id,)
        ).fetchone()
        version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]

    assert conversation["title"] == "Conversation conservée"
    assert message["content"] == "Message conservé"
    assert version == CURRENT_SCHEMA_VERSION


def test_fresh_database_contains_all_job_constraints(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()

    with closing(database.connect()) as connection:
        tables = {
            row["name"]: row["sql"]
            for row in connection.execute(
                """
                SELECT name, sql FROM sqlite_master
                WHERE type = 'table' AND name IN ('jobs', 'job_events')
                """
            )
        }
        foreign_keys = {
            (row["table"], row["from"], row["on_delete"])
            for row in connection.execute("PRAGMA foreign_key_list(jobs)")
        }
        indexes = {row["name"] for row in connection.execute("PRAGMA index_list(jobs)")}

    assert set(tables) == {"jobs", "job_events"}
    for job_type in (
        "chat_answer",
        "weekly_maintenance",
        "deep_research",
        "corpus_ingestion",
    ):
        assert f"'{job_type}'" in tables["jobs"]
    assert "'reranking'" in tables["jobs"]
    assert "'reranking'" in tables["job_events"]
    for chat_step in (
        "planning",
        "evidence_selection",
        "coverage",
        "figure_analysis",
        "generation",
    ):
        assert f"'{chat_step}'" in tables["jobs"]
        assert f"'{chat_step}'" in tables["job_events"]
    assert "CHECK(json_valid(payload_json))" in tables["jobs"]
    assert "UNIQUE(conversation_id, client_request_id)" in tables["jobs"]
    assert {
        ("chat_conversations", "conversation_id", "CASCADE"),
        ("chat_messages", "user_message_id", "CASCADE"),
        ("chat_messages", "result_message_id", "SET NULL"),
    } <= foreign_keys
    assert {
        "idx_jobs_claim",
        "idx_jobs_conversation",
        "idx_single_active_weekly_maintenance",
    } <= indexes
