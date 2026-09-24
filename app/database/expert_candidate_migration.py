"""Immutable candidate records linked to deterministic expert diagnoses."""

EXPERT_CANDIDATE_MIGRATION = """
CREATE TABLE IF NOT EXISTS expert_candidates (
    id TEXT PRIMARY KEY,
    diagnosis_id TEXT NOT NULL REFERENCES expert_diagnoses(id) ON DELETE RESTRICT,
    base_release_id TEXT NOT NULL REFERENCES expert_releases(id) ON DELETE RESTRICT,
    candidate_release_id TEXT NOT NULL UNIQUE REFERENCES expert_releases(id) ON DELETE RESTRICT,
    diff_sha256 TEXT NOT NULL CHECK(
        length(diff_sha256) = 64 AND diff_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    state TEXT NOT NULL CHECK(state IN (
        'draft', 'structurally_valid', 'evaluating', 'awaiting_review', 'approved', 'activated',
        'needs_expert', 'evaluation_failed', 'inconclusive', 'rejected', 'superseded'
    )),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 2),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(diagnosis_id, diff_sha256)
);

CREATE INDEX IF NOT EXISTS idx_expert_candidates_diagnosis
    ON expert_candidates(diagnosis_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_expert_candidates_state
    ON expert_candidates(state, created_at);
"""
