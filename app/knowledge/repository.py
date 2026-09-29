"""Immutable local candidate packages. Importing never changes the active release."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import ValidationError

from app import __version__
from app.database.migrations import CURRENT_SCHEMA_VERSION
from app.database.sqlite import Database
from app.knowledge.contracts import ImmutableModel, Sha256, canonical_json
from app.knowledge.models import KNOWLEDGE_ITEM_ADAPTER, KnowledgePackage
from app.knowledge.validation import validate_package

STORAGE_SCHEMA_VERSION = 35


class KnowledgeRepositoryError(ValueError):
    """Stable, content-free repository error codes."""


class StoredRelease(ImmutableModel):
    id: UUID
    package_sha256: Sha256
    state: Literal["candidate", "eligible", "retired", "revoked"]
    base_release_id: UUID | None
    created_at: datetime


class ActiveRelease(ImmutableModel):
    release_id: UUID | None
    generation: int


def assert_package_compatible(package: KnowledgePackage) -> None:
    if tuple(map(int, package.minimum_app_version.split("."))) > tuple(
        map(int, __version__.split("."))
    ):
        raise KnowledgeRepositoryError("incompatible_app_version")
    if package.minimum_schema_version > CURRENT_SCHEMA_VERSION:
        raise KnowledgeRepositoryError("incompatible_schema_version")


class KnowledgeRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def import_candidate(
        self, package: KnowledgePackage, *, base_release_id: UUID | None = None
    ) -> StoredRelease:
        """Validate the package and persist it as a candidate without activating its content."""

        try:
            package = KnowledgePackage.model_validate(package.model_dump(mode="python"))
        except ValidationError as error:
            raise KnowledgeRepositoryError("invalid_package") from error
        report = validate_package(package)
        if not report.structurally_valid:
            raise KnowledgeRepositoryError("invalid_package")
        assert_package_compatible(package)
        release_id = str(
            uuid5(NAMESPACE_URL, "ciderscholar:expert-memory:" + package.package_sha256)
        )
        base_id = str(base_release_id) if base_release_id is not None else None
        # Read-only preflight refuses absent/old databases before connect() can create
        # a database or change its journal mode. Recheck under the write lock below.
        with self.database.read_session() as session:
            self._assert_storage_ready(session.connection)
        with self.database.transaction() as connection:
            self._assert_storage_ready(connection)
            existing = connection.execute(
                "SELECT * FROM expert_releases WHERE package_sha256 = ?", (package.package_sha256,)
            ).fetchone()
            if existing is not None:
                if existing["base_release_id"] != base_id:
                    raise KnowledgeRepositoryError("candidate_base_conflict")
                self._load_package(connection, existing)
                return self._stored_release(existing)
            if base_id is not None:
                base = connection.execute(
                    "SELECT * FROM expert_releases WHERE id = ?", (base_id,)
                ).fetchone()
                if base is None:
                    raise KnowledgeRepositoryError("base_release_missing")
                self._load_package(connection, base)
            created_at = datetime.now(UTC).isoformat()
            metadata = package.model_dump(mode="json", exclude={"items"})
            metadata["item_hashes"] = {item.id: item.content_sha256 for item in package.items}
            connection.execute(
                """INSERT INTO expert_releases(id, package_sha256, schema_version,
                    app_min_version, created_at, base_release_id, manifest_json, state)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate')""",
                (
                    release_id,
                    package.package_sha256,
                    package.schema_version,
                    package.minimum_app_version,
                    created_at,
                    base_id,
                    canonical_json(metadata),
                ),
            )
            for item in package.items:
                connection.execute(
                    """INSERT INTO expert_release_items(release_id, item_id, revision, kind,
                           content_sha256, payload_json) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        release_id,
                        item.id,
                        item.revision,
                        item.kind,
                        item.content_sha256,
                        canonical_json(item.model_dump(mode="json")),
                    ),
                )
            for item in package.items:
                connection.executemany(
                    """INSERT INTO expert_release_dependencies(release_id, item_id, dependency_id)
                       VALUES (?, ?, ?)""",
                    ((release_id, item.id, dependency) for dependency in item.depends_on),
                )
            connection.execute(
                """INSERT INTO expert_release_events(id, from_release_id, to_release_id,
                    event_type, reason, created_at) VALUES (?, ?, ?, 'imported', ?, ?)""",
                (
                    str(uuid4()),
                    base_id,
                    release_id,
                    "candidate_imported_without_activation",
                    created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM expert_releases WHERE id = ?", (release_id,)
            ).fetchone()
            assert row is not None
            self._load_package(connection, row)
            return self._stored_release(row)

    def resolve_active_release(self) -> ActiveRelease:
        with self.database.read_session() as session:
            row = session.connection.execute(
                "SELECT release_id, generation FROM expert_active_release WHERE singleton = 1"
            ).fetchone()
            if row is None:
                raise KnowledgeRepositoryError("active_release_state_missing")
            if row["release_id"] is not None:
                release = session.connection.execute(
                    "SELECT * FROM expert_releases WHERE id = ?", (row["release_id"],)
                ).fetchone()
                if release is None or release["state"] != "eligible":
                    raise KnowledgeRepositoryError("active_release_ineligible")
                self._load_package(session.connection, release)
            return ActiveRelease(release_id=row["release_id"], generation=row["generation"])

    def load_release(self, release_id: UUID) -> tuple[StoredRelease, KnowledgePackage]:
        """Read a candidate for inspection; callers cannot mistake it for an active release."""

        with self.database.read_session() as session:
            row = session.connection.execute(
                "SELECT * FROM expert_releases WHERE id = ?", (str(release_id),)
            ).fetchone()
            if row is None:
                raise KnowledgeRepositoryError("release_missing")
            return self._stored_release(row), self._load_package(session.connection, row)

    @staticmethod
    def _stored_release(row: sqlite3.Row) -> StoredRelease:
        return StoredRelease.model_validate(
            {
                key: row[key]
                for key in ("id", "package_sha256", "state", "base_release_id", "created_at")
            }
        )

    @staticmethod
    def _assert_storage_ready(connection: sqlite3.Connection) -> None:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        required = {
            "schema_version",
            "expert_releases",
            "expert_release_items",
            "expert_release_dependencies",
            "expert_active_release",
            "expert_release_events",
        }
        if not required <= tables:
            raise KnowledgeRepositoryError("migration_required_before_import")
        version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        if version is None or version < STORAGE_SCHEMA_VERSION:
            raise KnowledgeRepositoryError("migration_required_before_import")
        if version > CURRENT_SCHEMA_VERSION:
            raise KnowledgeRepositoryError("incompatible_schema_version")

    @staticmethod
    def _load_package(connection: sqlite3.Connection, row: sqlite3.Row) -> KnowledgePackage:
        try:
            metadata = json.loads(row["manifest_json"])
            expected_hashes = metadata.pop("item_hashes")
            items = []
            rows = connection.execute(
                "SELECT * FROM expert_release_items WHERE release_id = ? ORDER BY item_id",
                (row["id"],),
            ).fetchall()
            for stored in rows:
                item = KNOWLEDGE_ITEM_ADAPTER.validate_json(stored["payload_json"])
                if (item.id, item.revision, item.kind, item.content_sha256) != (
                    stored["item_id"],
                    stored["revision"],
                    stored["kind"],
                    stored["content_sha256"],
                ):
                    raise KnowledgeRepositoryError("stored_item_mismatch")
                items.append(item)
            package = KnowledgePackage(**metadata, items=items)
            if (
                expected_hashes != {item.id: item.content_sha256 for item in package.items}
                or package.package_sha256 != row["package_sha256"]
                or package.schema_version != row["schema_version"]
                or package.minimum_app_version != row["app_min_version"]
                or str(uuid5(NAMESPACE_URL, "ciderscholar:expert-memory:" + package.package_sha256))
                != row["id"]
            ):
                raise KnowledgeRepositoryError("stored_package_mismatch")
            edges = connection.execute(
                """SELECT item_id, dependency_id FROM expert_release_dependencies
                   WHERE release_id = ? ORDER BY item_id, dependency_id""",
                (row["id"],),
            ).fetchall()
            expected_edges = sorted(
                (item.id, dependency) for item in package.items for dependency in item.depends_on
            )
            if [tuple(edge) for edge in edges] != expected_edges or not validate_package(
                package
            ).structurally_valid:
                raise KnowledgeRepositoryError("stored_graph_mismatch")
            assert_package_compatible(package)
            return package
        except (KeyError, TypeError, json.JSONDecodeError, ValidationError) as error:
            raise KnowledgeRepositoryError("invalid_stored_package") from error
