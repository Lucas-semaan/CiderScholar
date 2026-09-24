"""Append-only human reviews and activation receipts for expert candidates."""

EXPERT_REVIEW_MIGRATION = """
CREATE TABLE IF NOT EXISTS expert_reviews (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES expert_candidates(id) ON DELETE RESTRICT,
    evaluation_id TEXT NOT NULL REFERENCES expert_evaluations(id) ON DELETE RESTRICT,
    client_request_id TEXT NOT NULL CHECK(length(client_request_id) = 36),
    candidate_sha256 TEXT NOT NULL CHECK(
        length(candidate_sha256) = 64 AND candidate_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    evaluation_sha256 TEXT NOT NULL CHECK(
        length(evaluation_sha256) = 64 AND evaluation_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    decision TEXT NOT NULL CHECK(decision IN ('approve', 'reject', 'needs_changes')),
    reviewer_label TEXT NOT NULL CHECK(length(trim(reviewer_label)) BETWEEN 1 AND 120),
    reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 2000),
    created_at TEXT NOT NULL,
    UNIQUE(candidate_id, client_request_id)
);

CREATE INDEX IF NOT EXISTS idx_expert_reviews_candidate
    ON expert_reviews(candidate_id, created_at DESC);

CREATE TABLE IF NOT EXISTS expert_activation_events (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES expert_candidates(id) ON DELETE RESTRICT,
    review_id TEXT NOT NULL REFERENCES expert_reviews(id) ON DELETE RESTRICT,
    from_release_id TEXT REFERENCES expert_releases(id) ON DELETE RESTRICT,
    to_release_id TEXT NOT NULL REFERENCES expert_releases(id) ON DELETE RESTRICT,
    previous_generation INTEGER NOT NULL CHECK(previous_generation >= 0),
    generation INTEGER NOT NULL CHECK(generation > previous_generation),
    client_request_id TEXT NOT NULL CHECK(length(client_request_id) = 36),
    candidate_sha256 TEXT NOT NULL CHECK(
        length(candidate_sha256) = 64 AND candidate_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    evaluation_sha256 TEXT NOT NULL CHECK(
        length(evaluation_sha256) = 64 AND evaluation_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL,
    UNIQUE(candidate_id, client_request_id),
    UNIQUE(generation)
);

CREATE INDEX IF NOT EXISTS idx_expert_activation_candidate
    ON expert_activation_events(candidate_id, created_at DESC);
"""
