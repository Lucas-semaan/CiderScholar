"""Immutable deterministic diagnoses linked to private correction revisions."""

EXPERT_DIAGNOSIS_MIGRATION = """
CREATE TABLE IF NOT EXISTS expert_diagnoses (
    id TEXT PRIMARY KEY,
    correction_id TEXT NOT NULL REFERENCES expert_corrections(id) ON DELETE CASCADE,
    correction_revision INTEGER NOT NULL CHECK(correction_revision >= 1),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND length(payload_json) <= 262144),
    diagnosis_sha256 TEXT NOT NULL CHECK(
        length(diagnosis_sha256) = 64 AND diagnosis_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL,
    UNIQUE(correction_id, correction_revision, diagnosis_sha256)
);

CREATE INDEX IF NOT EXISTS idx_expert_diagnoses_correction
    ON expert_diagnoses(correction_id, correction_revision, created_at);
"""
