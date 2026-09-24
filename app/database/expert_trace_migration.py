"""Durable bounded manifests for expert-memory job attempts."""

EXPERT_TRACE_MIGRATION = """
CREATE TABLE IF NOT EXISTS expert_run_manifests (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 3),
    release_id TEXT REFERENCES expert_releases(id) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND length(payload_json) <= 2097152),
    manifest_sha256 TEXT NOT NULL CHECK(
        length(manifest_sha256) = 64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    state TEXT NOT NULL CHECK(
        state IN ('running', 'succeeded', 'failed', 'cancelled', 'incomplete')
    ),
    result_message_id TEXT REFERENCES chat_messages(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, attempt)
);

CREATE INDEX IF NOT EXISTS idx_expert_run_manifests_job
    ON expert_run_manifests(job_id, attempt);
CREATE INDEX IF NOT EXISTS idx_expert_run_manifests_state
    ON expert_run_manifests(state);
"""
