"""Private expert-correction proposals linked to assistant messages and traces."""

EXPERT_FEEDBACK_MIGRATION = """
CREATE TABLE IF NOT EXISTS expert_corrections (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    manifest_id TEXT REFERENCES expert_run_manifests(id) ON DELETE SET NULL,
    client_request_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND length(payload_json) <= 262144),
    status TEXT NOT NULL CHECK(status IN (
        'submitted', 'diagnosis_incomplete', 'diagnosed', 'needs_expert',
        'candidate_ready', 'resolved', 'rejected', 'withdrawn'
    )),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(message_id, client_request_id)
);

CREATE INDEX IF NOT EXISTS idx_expert_corrections_message
    ON expert_corrections(message_id, created_at);
CREATE INDEX IF NOT EXISTS idx_expert_corrections_status
    ON expert_corrections(status, created_at);

"""

EXPERT_FEEDBACK_REVISIONS_MIGRATION = """
CREATE TABLE IF NOT EXISTS expert_correction_revisions (
    id TEXT PRIMARY KEY,
    correction_id TEXT NOT NULL REFERENCES expert_corrections(id) ON DELETE CASCADE,
    client_request_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND length(payload_json) <= 262144),
    status TEXT NOT NULL CHECK(status IN (
        'submitted', 'diagnosis_incomplete', 'diagnosed', 'needs_expert',
        'candidate_ready', 'resolved', 'rejected', 'withdrawn'
    )),
    created_at TEXT NOT NULL,
    UNIQUE(correction_id, revision),
    UNIQUE(correction_id, client_request_id)
);

CREATE INDEX IF NOT EXISTS idx_expert_correction_revisions_correction
    ON expert_correction_revisions(correction_id, revision);
"""
