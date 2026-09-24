"""Durable, hash-bound records for the validation-only expert pilot."""

EXPERT_PILOT_MIGRATION = """
CREATE TABLE IF NOT EXISTS expert_pilots (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES expert_candidates(id) ON DELETE RESTRICT,
    evaluation_id TEXT NOT NULL REFERENCES expert_evaluations(id) ON DELETE RESTRICT,
    plan_json TEXT NOT NULL CHECK(
        json_valid(plan_json) AND length(plan_json) <= 262144
    ),
    plan_sha256 TEXT NOT NULL CHECK(
        length(plan_sha256) = 64 AND plan_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    audit_json TEXT CHECK(
        audit_json IS NULL OR (json_valid(audit_json) AND length(audit_json) <= 262144)
    ),
    audit_sha256 TEXT CHECK(
        audit_sha256 IS NULL OR (
            length(audit_sha256) = 64 AND audit_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    attestation_json TEXT CHECK(
        attestation_json IS NULL OR (
            json_valid(attestation_json) AND length(attestation_json) <= 131072
        )
    ),
    attestation_sha256 TEXT CHECK(
        attestation_sha256 IS NULL OR (
            length(attestation_sha256) = 64 AND attestation_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    state TEXT NOT NULL CHECK(state IN ('planned', 'ready', 'blocked', 'attested')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(candidate_id, evaluation_id, plan_sha256)
);

CREATE INDEX IF NOT EXISTS idx_expert_pilots_candidate
    ON expert_pilots(candidate_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_expert_pilots_state
    ON expert_pilots(state, updated_at DESC);
"""

EXPERT_PILOT_OBSERVATIONS_MIGRATION = """
CREATE TABLE IF NOT EXISTS expert_pilot_observations (
    id TEXT PRIMARY KEY,
    pilot_id TEXT NOT NULL REFERENCES expert_pilots(id) ON DELETE RESTRICT,
    case_sha256 TEXT NOT NULL CHECK(
        length(case_sha256) = 64 AND case_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    protocol_sha256 TEXT NOT NULL CHECK(
        length(protocol_sha256) = 64 AND protocol_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    observation_json TEXT NOT NULL CHECK(
        json_valid(observation_json) AND length(observation_json) <= 131072
    ),
    observation_sha256 TEXT NOT NULL CHECK(
        length(observation_sha256) = 64 AND observation_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL,
    UNIQUE(pilot_id, case_sha256)
);

CREATE INDEX IF NOT EXISTS idx_expert_pilot_observations_pilot
    ON expert_pilot_observations(pilot_id, created_at, id);
"""
