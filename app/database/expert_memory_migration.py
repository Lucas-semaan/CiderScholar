"""Additive expert-package storage; scientific data and active jobs are untouched."""

# Leave this transaction open: ensure_current writes the schema version in the same
# transaction, and Database.initialize commits it. A failed migration rolls back both.
EXPERT_MEMORY_MIGRATION = """
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS expert_releases (
    id TEXT PRIMARY KEY,
    package_sha256 TEXT NOT NULL UNIQUE
        CHECK(length(package_sha256) = 64 AND package_sha256 NOT GLOB '*[^0-9a-f]*'),
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    app_min_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    base_release_id TEXT REFERENCES expert_releases(id) ON DELETE RESTRICT,
    manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
    state TEXT NOT NULL DEFAULT 'candidate'
        CHECK(state IN ('candidate', 'eligible', 'retired', 'revoked'))
);

CREATE TABLE IF NOT EXISTS expert_release_items (
    release_id TEXT NOT NULL REFERENCES expert_releases(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    kind TEXT NOT NULL CHECK(kind IN ('taxonomy', 'method_policy', 'gateway', 'route', 'recipe')),
    content_sha256 TEXT NOT NULL
        CHECK(length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    PRIMARY KEY(release_id, item_id)
);

CREATE TABLE IF NOT EXISTS expert_release_dependencies (
    release_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    dependency_id TEXT NOT NULL,
    PRIMARY KEY(release_id, item_id, dependency_id),
    FOREIGN KEY(release_id, item_id)
        REFERENCES expert_release_items(release_id, item_id) ON DELETE CASCADE,
    FOREIGN KEY(release_id, dependency_id)
        REFERENCES expert_release_items(release_id, item_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS expert_active_release (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    release_id TEXT REFERENCES expert_releases(id) ON DELETE RESTRICT,
    generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO expert_active_release(singleton, release_id, generation) VALUES (1, NULL, 0);

CREATE TABLE IF NOT EXISTS expert_release_events (
    id TEXT PRIMARY KEY,
    from_release_id TEXT REFERENCES expert_releases(id) ON DELETE RESTRICT,
    to_release_id TEXT NOT NULL REFERENCES expert_releases(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK(event_type IN ('imported')),
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expert_releases_base ON expert_releases(base_release_id);
CREATE INDEX IF NOT EXISTS idx_expert_dependencies_consumer
    ON expert_release_dependencies(release_id, dependency_id);
CREATE INDEX IF NOT EXISTS idx_expert_events_target ON expert_release_events(to_release_id);
CREATE INDEX IF NOT EXISTS idx_expert_events_source ON expert_release_events(from_release_id);
CREATE INDEX IF NOT EXISTS idx_expert_active_release ON expert_active_release(release_id);

CREATE TRIGGER IF NOT EXISTS expert_release_content_immutable
BEFORE UPDATE OF id, package_sha256, schema_version, app_min_version,
    created_at, base_release_id, manifest_json ON expert_releases
BEGIN SELECT RAISE(ABORT, 'expert release content is immutable'); END;

CREATE TRIGGER IF NOT EXISTS expert_item_immutable
BEFORE UPDATE ON expert_release_items
BEGIN SELECT RAISE(ABORT, 'expert item is immutable'); END;

CREATE TRIGGER IF NOT EXISTS expert_item_no_delete
BEFORE DELETE ON expert_release_items
BEGIN SELECT RAISE(ABORT, 'expert item is immutable'); END;

CREATE TRIGGER IF NOT EXISTS expert_item_no_late_insert
BEFORE INSERT ON expert_release_items
WHEN EXISTS(SELECT 1 FROM expert_release_events WHERE to_release_id = NEW.release_id)
BEGIN SELECT RAISE(ABORT, 'expert release is sealed'); END;

CREATE TRIGGER IF NOT EXISTS expert_dependency_immutable
BEFORE UPDATE ON expert_release_dependencies
BEGIN SELECT RAISE(ABORT, 'expert dependency is immutable'); END;

CREATE TRIGGER IF NOT EXISTS expert_dependency_no_delete
BEFORE DELETE ON expert_release_dependencies
BEGIN SELECT RAISE(ABORT, 'expert dependency is immutable'); END;

CREATE TRIGGER IF NOT EXISTS expert_dependency_no_late_insert
BEFORE INSERT ON expert_release_dependencies
WHEN EXISTS(SELECT 1 FROM expert_release_events WHERE to_release_id = NEW.release_id)
BEGIN SELECT RAISE(ABORT, 'expert release is sealed'); END;

CREATE TRIGGER IF NOT EXISTS expert_event_immutable
BEFORE UPDATE ON expert_release_events
BEGIN SELECT RAISE(ABORT, 'expert event is immutable'); END;

CREATE TRIGGER IF NOT EXISTS expert_event_append_only
BEFORE DELETE ON expert_release_events
BEGIN SELECT RAISE(ABORT, 'expert event is append-only'); END;
"""
