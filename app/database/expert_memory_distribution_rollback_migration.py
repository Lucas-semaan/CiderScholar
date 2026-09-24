"""Append-only rollback receipts for distributed expert-memory activations."""

EXPERT_MEMORY_DISTRIBUTION_ROLLBACK_MIGRATION = """
CREATE TABLE IF NOT EXISTS expert_memory_distribution_rollback_events (
    id TEXT PRIMARY KEY,
    distribution_id TEXT NOT NULL REFERENCES expert_memory_distributions(id) ON DELETE RESTRICT,
    from_release_id TEXT NOT NULL REFERENCES expert_releases(id) ON DELETE RESTRICT,
    to_release_id TEXT NOT NULL REFERENCES expert_releases(id) ON DELETE RESTRICT,
    previous_generation INTEGER NOT NULL CHECK(previous_generation >= 0),
    generation INTEGER NOT NULL CHECK(generation > previous_generation),
    client_request_id TEXT NOT NULL UNIQUE CHECK(length(client_request_id) = 36),
    from_sha256 TEXT NOT NULL CHECK(
        length(from_sha256) = 64 AND from_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    to_sha256 TEXT NOT NULL CHECK(
        length(to_sha256) = 64 AND to_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 2000),
    created_at TEXT NOT NULL,
    UNIQUE(generation)
);

CREATE INDEX IF NOT EXISTS idx_expert_memory_distribution_rollback_target
    ON expert_memory_distribution_rollback_events(to_release_id, created_at DESC);
"""
