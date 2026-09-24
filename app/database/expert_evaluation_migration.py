"""Immutable expert-evaluation plans and bounded reports."""

EXPERT_EVALUATION_MIGRATION = """
CREATE TABLE IF NOT EXISTS expert_evaluations (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES expert_candidates(id) ON DELETE RESTRICT,
    manifest_json TEXT NOT NULL CHECK(
        json_valid(manifest_json) AND length(manifest_json) <= 262144
    ),
    manifest_sha256 TEXT NOT NULL CHECK(
        length(manifest_sha256) = 64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    report_json TEXT CHECK(
        report_json IS NULL OR (json_valid(report_json) AND length(report_json) <= 524288)
    ),
    report_sha256 TEXT CHECK(
        report_sha256 IS NULL OR (
            length(report_sha256) = 64 AND report_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    state TEXT NOT NULL CHECK(state IN (
        'planned', 'running', 'passed', 'failed', 'inconclusive', 'cancelled'
    )),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(candidate_id, manifest_sha256)
);

CREATE INDEX IF NOT EXISTS idx_expert_evaluations_candidate
    ON expert_evaluations(candidate_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_expert_evaluations_state
    ON expert_evaluations(state, created_at);
"""
