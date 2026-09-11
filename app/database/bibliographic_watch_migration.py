"""SQLite table rebuild adding resumable bibliographic watch."""

from __future__ import annotations

import sqlite3


def add_bibliographic_watch(connection: sqlite3.Connection) -> None:
    """Extend the closed jobs contract without changing existing job data."""

    existing_tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    # Version 31 deliberately repairs databases that only contain the scientific
    # tables from an interrupted version-30 migration. Such a partial database has
    # no durable-job contract to rebuild; later startup repairs can still advance
    # its scientific schema safely.
    if not {"jobs", "job_events"} <= existing_tables:
        return

    connection.executescript(
        """
        DROP INDEX IF EXISTS idx_jobs_claim;
        DROP INDEX IF EXISTS idx_jobs_conversation;
        DROP INDEX IF EXISTS idx_job_events_job;
        DROP INDEX IF EXISTS idx_single_active_weekly_maintenance;

        ALTER TABLE job_events RENAME TO job_events_v35;
        ALTER TABLE jobs RENAME TO jobs_v35;

        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL CHECK(
                type IN (
                    'chat_answer', 'weekly_maintenance', 'deep_research',
                    'long_synthesis', 'corpus_ingestion', 'bibliographic_watch'
                )
            ),
            state TEXT NOT NULL CHECK(
                state IN (
                    'queued', 'running', 'succeeded', 'failed',
                    'cancel_requested', 'cancelled'
                )
            ),
            step TEXT NOT NULL CHECK(
                step IN (
                    'waiting', 'planning', 'search', 'enrichment', 'reranking',
                    'evidence_selection', 'coverage', 'figure_analysis', 'generation',
                    'argo', 'validation', 'persistence', 'backup', 'suggestions',
                    'harvest', 'index', 'publish', 'evidence', 'verification',
                    'synthesis', 'ingestion'
                )
            ),
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
            priority INTEGER NOT NULL DEFAULT 100 CHECK(priority BETWEEN 0 AND 1000),
            attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 3),
            available_at TEXT NOT NULL,
            worker_id TEXT,
            lease_expires_at TEXT,
            heartbeat_at TEXT,
            conversation_id TEXT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
            user_message_id TEXT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
            result_message_id TEXT REFERENCES chat_messages(id) ON DELETE SET NULL,
            client_request_id TEXT NOT NULL CHECK(length(client_request_id) = 36),
            error_code TEXT CHECK(
                error_code IS NULL OR error_code IN (
                    'timeout', 'quota', 'authentication', 'validation'
                )
            ),
            error_message TEXT CHECK(
                error_message IS NULL OR length(error_message) BETWEEN 1 AND 300
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            UNIQUE(conversation_id, client_request_id),
            CHECK(
                state != 'running'
                OR (worker_id IS NOT NULL AND lease_expires_at IS NOT NULL)
            )
        );

        INSERT INTO jobs SELECT * FROM jobs_v35;

        CREATE TABLE job_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            state TEXT NOT NULL CHECK(
                state IN (
                    'queued', 'running', 'succeeded', 'failed',
                    'cancel_requested', 'cancelled'
                )
            ),
            step TEXT NOT NULL CHECK(
                step IN (
                    'waiting', 'planning', 'search', 'enrichment', 'reranking',
                    'evidence_selection', 'coverage', 'figure_analysis', 'generation',
                    'argo', 'validation', 'persistence', 'backup', 'suggestions',
                    'harvest', 'index', 'publish', 'evidence', 'verification',
                    'synthesis', 'ingestion'
                )
            ),
            technical_message TEXT CHECK(
                technical_message IS NULL OR length(technical_message) BETWEEN 1 AND 300
            ),
            created_at TEXT NOT NULL
        );

        INSERT INTO job_events SELECT * FROM job_events_v35;
        DROP TABLE job_events_v35;
        DROP TABLE jobs_v35;

        CREATE INDEX idx_jobs_claim
            ON jobs(state, priority, available_at, created_at);
        CREATE INDEX idx_jobs_conversation
            ON jobs(conversation_id, state, created_at DESC);
        CREATE INDEX idx_job_events_job
            ON job_events(job_id, created_at, id);
        CREATE TABLE bibliographic_watch_state (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL CHECK(json_valid(value_json))
        );
        CREATE TABLE bibliographic_watch_items (
            record_id TEXT PRIMARY KEY REFERENCES bibliographic_records(id) ON DELETE CASCADE,
            initially_ready INTEGER NOT NULL CHECK(initially_ready IN (0, 1)),
            added_by_job TEXT,
            attempted_at TEXT,
            pending INTEGER NOT NULL DEFAULT 1 CHECK(pending IN (0, 1))
        );
        CREATE TABLE bibliographic_watch_runs (
            job_id TEXT PRIMARY KEY,
            checkpoint_json TEXT NOT NULL CHECK(json_valid(checkpoint_json)),
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX idx_single_active_bibliographic_watch
            ON jobs(type) WHERE type = 'bibliographic_watch'
            AND state IN ('queued', 'running', 'cancel_requested');
        CREATE UNIQUE INDEX idx_single_active_weekly_maintenance
            ON jobs(type)
            WHERE type = 'weekly_maintenance'
              AND state IN ('queued', 'running', 'cancel_requested');
        """
    )
