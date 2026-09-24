"""Append-only receipts for explicit one-step expert-memory rollback."""

EXPERT_ROLLBACK_MIGRATION = """
CREATE TABLE IF NOT EXISTS expert_rollback_events (
    id TEXT PRIMARY KEY,
    from_release_id TEXT NOT NULL REFERENCES expert_releases(id) ON DELETE RESTRICT,
    to_release_id TEXT NOT NULL REFERENCES expert_releases(id) ON DELETE RESTRICT,
    candidate_id TEXT NOT NULL REFERENCES expert_candidates(id) ON DELETE RESTRICT,
    client_request_id TEXT NOT NULL UNIQUE CHECK(length(client_request_id) = 36),
    from_sha256 TEXT NOT NULL CHECK(
        length(from_sha256) = 64 AND from_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    to_sha256 TEXT NOT NULL CHECK(
        length(to_sha256) = 64 AND to_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    previous_generation INTEGER NOT NULL CHECK(previous_generation >= 0),
    generation INTEGER NOT NULL CHECK(generation > previous_generation),
    reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 2000),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expert_rollback_target
    ON expert_rollback_events(to_release_id, created_at DESC);
"""
