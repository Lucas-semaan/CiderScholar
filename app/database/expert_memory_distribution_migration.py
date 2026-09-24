"""Local proposal and activation receipts for distributed expert-memory packages."""

EXPERT_MEMORY_DISTRIBUTION_MIGRATION = """
CREATE TABLE IF NOT EXISTS expert_memory_distributions (
    id TEXT PRIMARY KEY,
    release_id TEXT NOT NULL UNIQUE REFERENCES expert_releases(id) ON DELETE RESTRICT,
    package_sha256 TEXT NOT NULL CHECK(
        length(package_sha256) = 64 AND package_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    package_manifest_sha256 TEXT NOT NULL CHECK(
        length(package_manifest_sha256) = 64
        AND package_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    approved_review_sha256 TEXT NOT NULL CHECK(
        length(approved_review_sha256) = 64
        AND approved_review_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    state TEXT NOT NULL CHECK(state IN (
        'imported', 'proposed', 'approved', 'rejected', 'activated'
    )),
    reviewer_label TEXT CHECK(
        reviewer_label IS NULL OR length(trim(reviewer_label)) BETWEEN 1 AND 120
    ),
    reason TEXT CHECK(reason IS NULL OR length(trim(reason)) BETWEEN 1 AND 2000),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expert_memory_distributions_state
    ON expert_memory_distributions(state, updated_at DESC);

CREATE TABLE IF NOT EXISTS expert_memory_distribution_events (
    id TEXT PRIMARY KEY,
    distribution_id TEXT NOT NULL REFERENCES expert_memory_distributions(id) ON DELETE RESTRICT,
    from_release_id TEXT REFERENCES expert_releases(id) ON DELETE RESTRICT,
    to_release_id TEXT NOT NULL REFERENCES expert_releases(id) ON DELETE RESTRICT,
    previous_generation INTEGER NOT NULL CHECK(previous_generation >= 0),
    generation INTEGER NOT NULL CHECK(generation > previous_generation),
    client_request_id TEXT NOT NULL UNIQUE CHECK(length(client_request_id) = 36),
    package_sha256 TEXT NOT NULL CHECK(
        length(package_sha256) = 64 AND package_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL,
    UNIQUE(generation)
);

CREATE INDEX IF NOT EXISTS idx_expert_memory_distribution_events_target
    ON expert_memory_distribution_events(to_release_id, created_at DESC);
"""
