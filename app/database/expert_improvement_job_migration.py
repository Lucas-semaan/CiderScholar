"""SQLite rebuild adding the private expert-improvement durable job type."""

from __future__ import annotations

import sqlite3


def add_expert_improvement_job_contract(connection: sqlite3.Connection) -> None:
    """Add one closed job type while preserving all existing queue rows."""

    existing_tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not {"jobs", "job_events"} <= existing_tables:
        return

    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.executescript(
            """
        DROP INDEX IF EXISTS idx_jobs_claim;
        DROP INDEX IF EXISTS idx_jobs_conversation;
        DROP INDEX IF EXISTS idx_job_events_job;
        DROP INDEX IF EXISTS idx_single_active_weekly_maintenance;
        CREATE TABLE jobs_v51 (

            id TEXT PRIMARY KEY,
            type TEXT NOT NULL CHECK(
                type IN (
                    'chat_answer', 'weekly_maintenance', 'deep_research',
                    'long_synthesis', 'corpus_ingestion', 'bibliographic_watch',
                    'expert_improvement'
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

        INSERT INTO jobs_v51 SELECT * FROM jobs;
        DROP TABLE jobs;
        ALTER TABLE jobs_v51 RENAME TO jobs;

        CREATE TABLE job_events_v51 (
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

        INSERT INTO job_events_v51 SELECT * FROM job_events;
        DROP TABLE job_events;
        ALTER TABLE job_events_v51 RENAME TO job_events;

        CREATE INDEX idx_jobs_claim
            ON jobs(state, priority, available_at, created_at);
        CREATE INDEX idx_jobs_conversation
            ON jobs(conversation_id, state, created_at DESC);
        CREATE INDEX idx_job_events_job
            ON job_events(job_id, created_at, id);
        CREATE UNIQUE INDEX idx_single_active_weekly_maintenance
            ON jobs(type)
            WHERE type = 'weekly_maintenance'
              AND state IN ('queued', 'running', 'cancel_requested');
        CREATE UNIQUE INDEX idx_single_active_bibliographic_watch
            ON jobs(type)
            WHERE type = 'bibliographic_watch'
              AND state IN ('queued', 'running', 'cancel_requested');
        """
        )
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
