"""Build and verify transportable expert-memory packages without private data."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import Field, field_validator

from app import __version__
from app.corpus_packages.hashing import sha256_file
from app.corpus_packages.signatures import _fingerprint, _ssh_keygen
from app.database.migrations import CURRENT_SCHEMA_VERSION
from app.database.sqlite import Database
from app.knowledge.contracts import ImmutableModel, Sha256, Version, canonical_json, content_hash
from app.knowledge.models import KnowledgePackage
from app.knowledge.repository import KnowledgeRepository, StoredRelease

EXPERT_MEMORY_NAMESPACE = "ciderscholar-expert-memory-v1"
EXPERT_MEMORY_FILENAME = "expert-memory.json"
EXPERT_MEMORY_SIGNATURE_FILENAME = "expert-memory.json.sig"
EXPERT_MEMORY_SIGNATURE_MANIFEST = "signatures.json"


class ExpertMemoryPackageError(ValueError):
    """The package is not approved, compatible, complete or trusted."""


class ExpertMemoryPackage(ImmutableModel):
    schema_version: Literal[1] = 1
    package_kind: Literal["expert_memory"] = "expert_memory"
    package_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,119}$")
    version: Version
    minimum_app_version: Version
    minimum_schema_version: int = Field(strict=True, ge=1)
    source_release_sha256: Sha256
    approved_review_sha256: Sha256
    knowledge: KnowledgePackage
    published_at: datetime
    manifest_sha256: Sha256 = "0" * 64

    @property
    def package_sha256(self) -> str:
        return self.knowledge.package_sha256

    @property
    def computed_manifest_sha256(self) -> str:
        return content_hash(self.model_dump(mode="json", exclude={"manifest_sha256"}))

    @field_validator("published_at")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("package publication timestamp must be timezone-aware")
        return value.astimezone(UTC)


class ExpertMemorySignature(ImmutableModel):
    schema_version: Literal[1] = 1
    algorithm: Literal["ssh-ed25519"] = "ssh-ed25519"
    namespace: Literal["ciderscholar-expert-memory-v1"] = EXPERT_MEMORY_NAMESPACE
    signer_identity: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]{2,127}$")
    public_key_fingerprint: str = Field(pattern=r"^SHA256:[A-Za-z0-9+/]{20,100}$")
    artifact: Literal["expert-memory.json"] = EXPERT_MEMORY_FILENAME
    artifact_sha256: Sha256
    signature_file: Literal["expert-memory.json.sig"] = EXPERT_MEMORY_SIGNATURE_FILENAME


class ExpertMemoryPackageBuildReport(ImmutableModel):
    package_directory: str
    package_path: str
    package: ExpertMemoryPackage
    reused_existing: bool = False


class ExpertMemoryStagedPackage(ImmutableModel):
    staging_directory: str
    package_path: str
    package: ExpertMemoryPackage
    reused_existing: bool = False


def build_approved_expert_memory_package(
    database: Database,
    candidate_id: UUID,
    *,
    output_root: str | Path,
    now: datetime | None = None,
) -> ExpertMemoryPackageBuildReport:
    """Export only a candidate with an exact approved human review."""

    with database.read_session() as session:
        row = session.connection.execute(
            """
            SELECT c.candidate_release_id, c.state, release.package_sha256,
                   review.candidate_sha256, review.evaluation_sha256,
                   review.id AS review_id
            FROM expert_candidates AS c
            JOIN expert_releases AS release ON release.id = c.candidate_release_id
            JOIN expert_reviews AS review
              ON review.id = (
                  SELECT r.id FROM expert_reviews AS r
                  WHERE r.candidate_id = c.id AND r.decision = 'approve'
                  ORDER BY r.created_at DESC, r.id DESC LIMIT 1
              )
            WHERE c.id = ?
            """,
            (str(candidate_id),),
        ).fetchone()
    if row is None:
        raise ExpertMemoryPackageError("approved_review_not_found")
    if row["state"] not in {"approved", "activated"}:
        raise ExpertMemoryPackageError("candidate_not_approved")
    if row["candidate_sha256"] != row["package_sha256"]:
        raise ExpertMemoryPackageError("approved_candidate_hash_mismatch")
    review_hash = content_hash(
        {
            "candidate_id": str(candidate_id),
            "candidate_sha256": row["candidate_sha256"],
            "evaluation_sha256": row["evaluation_sha256"],
            "decision": "approve",
            "review_id": row["review_id"],
        }
    )
    release, knowledge = KnowledgeRepository(database).load_release(
        UUID(str(row["candidate_release_id"]))
    )
    if release.package_sha256 != row["package_sha256"]:
        raise ExpertMemoryPackageError("stored_release_hash_mismatch")
    package = ExpertMemoryPackage(
        package_id="ciderscholar.expert_memory",
        version=knowledge.version,
        minimum_app_version=knowledge.minimum_app_version,
        minimum_schema_version=knowledge.minimum_schema_version,
        source_release_sha256=release.package_sha256,
        approved_review_sha256=review_hash,
        knowledge=knowledge,
        published_at=(now or datetime.now(UTC)).astimezone(UTC),
    )
    package = package.model_copy(update={"manifest_sha256": package.computed_manifest_sha256})
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / package.package_sha256
    package_path = destination / EXPERT_MEMORY_FILENAME
    if destination.exists():
        existing = verify_expert_memory_package(destination)
        if existing != package:
            raise ExpertMemoryPackageError("immutable_package_conflict")
        return ExpertMemoryPackageBuildReport(
            package_directory=str(destination),
            package_path=str(package_path),
            package=existing,
            reused_existing=True,
        )
    with tempfile.TemporaryDirectory(prefix=".expert-memory-", dir=root) as temporary:
        stage = Path(temporary) / package.package_sha256
        stage.mkdir()
        (stage / EXPERT_MEMORY_FILENAME).write_text(
            canonical_json(package.model_dump(mode="json")) + "\n", encoding="utf-8"
        )
        stage.replace(destination)
    return ExpertMemoryPackageBuildReport(
        package_directory=str(destination),
        package_path=str(package_path),
        package=package,
    )


def sign_expert_memory_package(
    package_directory: str | Path,
    *,
    private_key: str | Path,
    signer_identity: str,
) -> ExpertMemorySignature:
    """Create a detached OpenSSH signature for the complete memory package file."""

    root = Path(package_directory).resolve()
    key = Path(private_key).resolve()
    public_key = key.with_suffix(key.suffix + ".pub")
    source = root / EXPERT_MEMORY_FILENAME
    signature = root / EXPERT_MEMORY_SIGNATURE_FILENAME
    if not key.is_file() or not public_key.is_file():
        raise ExpertMemoryPackageError("signing_keys_unavailable")
    if not source.is_file() or signature.exists():
        raise ExpertMemoryPackageError("package_artifact_unavailable_or_already_signed")
    subprocess.run(
        [
            _ssh_keygen(),
            "-Y",
            "sign",
            "-f",
            str(key),
            "-n",
            EXPERT_MEMORY_NAMESPACE,
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    if not signature.is_file():
        raise ExpertMemoryPackageError("signature_artifact_missing")
    signed = ExpertMemorySignature(
        signer_identity=signer_identity,
        public_key_fingerprint=_fingerprint(public_key),
        artifact_sha256=sha256_file(source),
    )
    (root / EXPERT_MEMORY_SIGNATURE_MANIFEST).write_text(
        json.dumps(signed.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return signed


def verify_expert_memory_package(
    package_directory: str | Path,
    *,
    allowed_signers: str | Path | None = None,
    require_signature: bool = False,
) -> ExpertMemoryPackage:
    """Verify content, compatibility metadata and optionally the trust signature."""

    root = Path(package_directory).resolve()
    source = root / EXPERT_MEMORY_FILENAME
    if not source.is_file():
        raise ExpertMemoryPackageError("package_artifact_missing")
    try:
        package = ExpertMemoryPackage.model_validate_json(source.read_bytes())
    except (OSError, ValueError) as error:
        raise ExpertMemoryPackageError("invalid_package_manifest") from error
    if package.package_sha256 != package.knowledge.package_sha256:
        raise ExpertMemoryPackageError("package_content_hash_mismatch")
    if package.manifest_sha256 != package.computed_manifest_sha256:
        raise ExpertMemoryPackageError("manifest_hash_mismatch")
    if package.source_release_sha256 != package.knowledge.package_sha256:
        raise ExpertMemoryPackageError("source_release_hash_mismatch")
    if tuple(map(int, package.minimum_app_version.split("."))) > tuple(
        map(int, __version__.split("."))
    ):
        raise ExpertMemoryPackageError("incompatible_app_version")
    if package.minimum_schema_version > CURRENT_SCHEMA_VERSION:
        raise ExpertMemoryPackageError("incompatible_schema_version")
    if require_signature or (root / EXPERT_MEMORY_SIGNATURE_MANIFEST).exists():
        if allowed_signers is None:
            raise ExpertMemoryPackageError("allowed_signers_required")
        _verify_signature(root, allowed_signers)
    return package


def stage_expert_memory_package(
    source_directory: str | Path,
    *,
    staging_root: str | Path,
    allowed_signers: str | Path | None = None,
    require_signature: bool = False,
) -> ExpertMemoryStagedPackage:
    """Copy a verified package into a content-addressed staging directory."""

    source = Path(source_directory).resolve()
    package = verify_expert_memory_package(
        source,
        allowed_signers=allowed_signers,
        require_signature=require_signature,
    )
    root = Path(staging_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / package.package_sha256
    destination_package = destination / EXPERT_MEMORY_FILENAME
    filenames = (
        EXPERT_MEMORY_FILENAME,
        EXPERT_MEMORY_SIGNATURE_FILENAME,
        EXPERT_MEMORY_SIGNATURE_MANIFEST,
    )
    if destination.exists():
        existing = verify_expert_memory_package(
            destination,
            allowed_signers=allowed_signers,
            require_signature=require_signature,
        )
        if existing != package:
            raise ExpertMemoryPackageError("staging_package_conflict")
        return ExpertMemoryStagedPackage(
            staging_directory=str(destination),
            package_path=str(destination_package),
            package=existing,
            reused_existing=True,
        )
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=root) as temporary:
        stage = Path(temporary) / package.package_sha256
        stage.mkdir()
        for filename in filenames:
            artifact = source / filename
            if artifact.is_file():
                shutil.copy2(artifact, stage / filename)
        stage.replace(destination)
    staged = verify_expert_memory_package(
        destination,
        allowed_signers=allowed_signers,
        require_signature=require_signature,
    )
    return ExpertMemoryStagedPackage(
        staging_directory=str(destination),
        package_path=str(destination_package),
        package=staged,
    )


def import_staged_expert_memory_package(
    database: Database,
    staged: ExpertMemoryStagedPackage,
    *,
    allowed_signers: str | Path | None = None,
    require_signature: bool = False,
) -> StoredRelease:
    """Verify again and import only as an inactive candidate release."""

    package = verify_expert_memory_package(
        staged.staging_directory,
        allowed_signers=allowed_signers,
        require_signature=require_signature,
    )
    if package != staged.package:
        raise ExpertMemoryPackageError("staged_package_changed")
    release = KnowledgeRepository(database).import_candidate(package.knowledge)
    register_imported_expert_memory_package(database, staged, release.id)
    return release


def register_imported_expert_memory_package(
    database: Database,
    staged: ExpertMemoryStagedPackage,
    release_id: UUID,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Record an imported package as a local, inactive distribution proposal."""

    package = staged.package
    distribution_id = uuid5(
        NAMESPACE_URL, "ciderscholar:expert-memory-distribution:" + package.package_sha256
    )
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    with database.transaction() as connection:
        release = connection.execute(
            "SELECT id, package_sha256 FROM expert_releases WHERE id = ?",
            (str(release_id),),
        ).fetchone()
        if release is None or release["package_sha256"] != package.package_sha256:
            raise ExpertMemoryPackageError("imported_release_hash_mismatch")
        existing = connection.execute(
            "SELECT * FROM expert_memory_distributions WHERE id = ?",
            (str(distribution_id),),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO expert_memory_distributions(
                    id, release_id, package_sha256, package_manifest_sha256,
                    approved_review_sha256, state, reviewer_label, reason,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'imported', NULL, NULL, ?, ?)
                """,
                (
                    str(distribution_id),
                    str(release_id),
                    package.package_sha256,
                    package.manifest_sha256,
                    package.approved_review_sha256,
                    timestamp,
                    timestamp,
                ),
            )
            existing = connection.execute(
                "SELECT * FROM expert_memory_distributions WHERE id = ?",
                (str(distribution_id),),
            ).fetchone()
    if existing is None:
        raise ExpertMemoryPackageError("distribution_disappeared")
    return _public_distribution(existing)


def get_expert_memory_distribution(
    database: Database, distribution_id: UUID
) -> dict[str, object] | None:
    with database.connect() as connection:
        row = _load_distribution(connection, distribution_id)
    return _public_distribution(row) if row is not None else None


def list_expert_memory_releases(
    database: Database,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    """List release identities and the active pointer without exposing package contents."""

    bounded_limit = max(1, min(limit, 100))
    clauses: list[str] = []
    parameters: list[object] = []
    if cursor is not None:
        try:
            created_at, release_id = cursor.rsplit("|", 1)
            UUID(release_id)
            datetime.fromisoformat(created_at)
        except (ValueError, TypeError) as error:
            raise ExpertMemoryPackageError("release_cursor_invalid") from error
        clauses.append("(r.created_at < ? OR (r.created_at = ? AND r.id < ?))")
        parameters.extend((created_at, created_at, release_id))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with database.connect() as connection:
        active = connection.execute(
            "SELECT release_id, generation FROM expert_active_release WHERE singleton = 1"
        ).fetchone()
        if active is None:
            raise ExpertMemoryPackageError("active_release_state_missing")
        rows = connection.execute(
            f"""
            SELECT r.id, r.package_sha256, r.state, r.base_release_id, r.created_at,
                   CASE WHEN a.release_id = r.id THEN 1 ELSE 0 END AS is_active,
                   CASE WHEN a.release_id = r.id THEN a.generation ELSE NULL END
                       AS active_generation
            FROM expert_releases AS r
            CROSS JOIN expert_active_release AS a
            {where}
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (*parameters, bounded_limit + 1),
        ).fetchall()
    has_more = len(rows) > bounded_limit
    visible = rows[:bounded_limit]
    releases = [
        {
            "id": UUID(str(row["id"])),
            "package_sha256": row["package_sha256"],
            "state": row["state"],
            "base_release_id": UUID(str(row["base_release_id"]))
            if row["base_release_id"] is not None
            else None,
            "created_at": datetime.fromisoformat(row["created_at"]),
            "active": bool(row["is_active"]),
            "active_generation": int(row["active_generation"])
            if row["active_generation"] is not None
            else None,
        }
        for row in visible
    ]
    next_cursor = None
    if has_more and visible:
        last = visible[-1]
        next_cursor = f"{last['created_at'].isoformat()}|{last['id']}"
    return {
        "releases": releases,
        "active_release_id": UUID(str(active["release_id"]))
        if active["release_id"] is not None
        else None,
        "active_generation": int(active["generation"]),
        "next_cursor": next_cursor,
    }


def list_expert_memory_distributions(
    database: Database,
    *,
    state: str | None = None,
    limit: int = 50,
) -> list[dict[str, object]]:
    """List distribution receipts without exposing package contents."""

    if state is not None and state not in {
        "imported",
        "proposed",
        "approved",
        "rejected",
        "activated",
        "rolled_back",
    }:
        raise ExpertMemoryPackageError("distribution_state_invalid")
    bounded_limit = max(1, min(limit, 100))
    clauses: list[str] = []
    parameters: list[object] = []
    if state == "rolled_back":
        clauses.append("d.rolled_back_at IS NOT NULL")
    elif state is not None:
        clauses.append("d.state = ?")
        parameters.append(state)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with database.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT d.*
            FROM expert_memory_distributions AS d
            {where}
            ORDER BY d.updated_at DESC, d.id DESC
            LIMIT ?
            """,
            (*parameters, bounded_limit),
        ).fetchall()
    return [_public_distribution(row) for row in rows]


def propose_expert_memory_distribution(
    database: Database,
    distribution_id: UUID,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Make an imported package visible as a proposal without activating it."""

    with database.transaction() as connection:
        row = _load_distribution(connection, distribution_id)
        if row is None:
            raise ExpertMemoryPackageError("distribution_not_found")
        if row["state"] not in {"imported", "proposed"}:
            raise ExpertMemoryPackageError("distribution_state_conflict")
        if row["state"] == "imported":
            connection.execute(
                """
                UPDATE expert_memory_distributions
                SET state = 'proposed', updated_at = ?
                WHERE id = ?
                """,
                ((now or datetime.now(UTC)).astimezone(UTC).isoformat(), str(distribution_id)),
            )
        row = _load_distribution(connection, distribution_id)
    if row is None:
        raise ExpertMemoryPackageError("distribution_disappeared")
    return _public_distribution(row)


def approve_expert_memory_distribution(
    database: Database,
    distribution_id: UUID,
    *,
    reviewer_label: str,
    reason: str,
    now: datetime | None = None,
) -> dict[str, object]:
    """Record local human approval; this still does not move the active pointer."""

    if not reviewer_label.strip() or not reason.strip():
        raise ExpertMemoryPackageError("distribution_approval_requires_reviewer_and_reason")
    with database.transaction() as connection:
        row = _load_distribution(connection, distribution_id)
        if row is None:
            raise ExpertMemoryPackageError("distribution_not_found")
        if row["state"] == "approved":
            if row["reviewer_label"] != reviewer_label or row["reason"] != reason:
                raise ExpertMemoryPackageError("distribution_approval_conflict")
        elif row["state"] == "proposed":
            connection.execute(
                """
                UPDATE expert_memory_distributions
                SET state = 'approved', reviewer_label = ?, reason = ?, updated_at = ?
                WHERE id = ? AND state = 'proposed'
                """,
                (
                    reviewer_label,
                    reason,
                    (now or datetime.now(UTC)).astimezone(UTC).isoformat(),
                    str(distribution_id),
                ),
            )
        else:
            raise ExpertMemoryPackageError("distribution_state_conflict")
        row = _load_distribution(connection, distribution_id)
    if row is None:
        raise ExpertMemoryPackageError("distribution_disappeared")
    return _public_distribution(row)


def activate_expert_memory_distribution(
    database: Database,
    distribution_id: UUID,
    *,
    client_request_id: UUID,
    expected_active_generation: int,
    expected_active_release_id: UUID | None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Atomically activate an explicitly approved distributed release."""

    timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    with database.transaction() as connection:
        existing_event = connection.execute(
            """
            SELECT * FROM expert_memory_distribution_events
            WHERE client_request_id = ?
            """,
            (str(client_request_id),),
        ).fetchone()
        if existing_event is not None:
            if (
                existing_event["distribution_id"] != str(distribution_id)
                or int(existing_event["previous_generation"]) != expected_active_generation
                or existing_event["from_release_id"]
                != (
                    str(expected_active_release_id)
                    if expected_active_release_id is not None
                    else None
                )
            ):
                raise ExpertMemoryPackageError("distribution_activation_request_conflict")
            return _public_distribution_event(existing_event)
        row = connection.execute(
            """
            SELECT d.*, r.state AS release_state, r.package_sha256,
                   active.release_id AS active_release_id, active.generation AS active_generation
            FROM expert_memory_distributions AS d
            JOIN expert_releases AS r ON r.id = d.release_id
            JOIN expert_active_release AS active ON active.singleton = 1
            WHERE d.id = ?
            """,
            (str(distribution_id),),
        ).fetchone()
        if row is None:
            raise ExpertMemoryPackageError("distribution_not_found")
        if row["state"] != "approved":
            raise ExpertMemoryPackageError("distribution_not_approved")
        if row["release_state"] != "candidate":
            raise ExpertMemoryPackageError("distribution_release_not_candidate")
        if row["active_generation"] != expected_active_generation:
            raise ExpertMemoryPackageError("active_generation_conflict")
        if row["active_release_id"] != (
            str(expected_active_release_id) if expected_active_release_id is not None else None
        ):
            raise ExpertMemoryPackageError("active_release_conflict")
        next_generation = expected_active_generation + 1
        if row["active_release_id"] is not None:
            connection.execute(
                "UPDATE expert_releases SET state = 'retired' WHERE id = ? AND state = 'eligible'",
                (row["active_release_id"],),
            )
        if (
            connection.execute(
                """
                UPDATE expert_releases SET state = 'eligible'
                WHERE id = ? AND state = 'candidate'
                """,
                (row["release_id"],),
            ).rowcount
            != 1
        ):
            raise ExpertMemoryPackageError("distribution_release_state_conflict")
        if (
            connection.execute(
                """
                UPDATE expert_active_release
                SET release_id = ?, generation = ?, updated_at = ?
                WHERE singleton = 1 AND generation = ?
                """,
                (row["release_id"], next_generation, timestamp, expected_active_generation),
            ).rowcount
            != 1
        ):
            raise ExpertMemoryPackageError("active_generation_conflict")
        connection.execute(
            """
            UPDATE expert_memory_distributions
            SET state = 'activated', updated_at = ?
            WHERE id = ?
            """,
            (timestamp, str(distribution_id)),
        )
        event_id = uuid4()
        connection.execute(
            """
            INSERT INTO expert_memory_distribution_events(
                id, distribution_id, from_release_id, to_release_id,
                previous_generation, generation, client_request_id,
                package_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(event_id),
                str(distribution_id),
                row["active_release_id"],
                row["release_id"],
                expected_active_generation,
                next_generation,
                str(client_request_id),
                row["package_sha256"],
                timestamp,
            ),
        )
        event = connection.execute(
            "SELECT * FROM expert_memory_distribution_events WHERE id = ?",
            (str(event_id),),
        ).fetchone()
    if event is None:
        raise ExpertMemoryPackageError("distribution_activation_disappeared")
    return _public_distribution_event(event)


def rollback_expert_memory_distribution(
    database: Database,
    distribution_id: UUID,
    *,
    client_request_id: UUID,
    target_release_id: UUID,
    target_release_sha256: str,
    expected_active_generation: int,
    expected_active_release_id: UUID,
    reason: str,
    now: datetime | None = None,
) -> dict[str, object]:
    """Explicitly restore the immediate parent recorded by one distribution event."""

    if not reason.strip():
        raise ExpertMemoryPackageError("rollback_reason_required")
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    with database.transaction() as connection:
        existing_event = connection.execute(
            """
            SELECT * FROM expert_memory_distribution_rollback_events
            WHERE client_request_id = ?
            """,
            (str(client_request_id),),
        ).fetchone()
        if existing_event is not None:
            if (
                existing_event["distribution_id"] != str(distribution_id)
                or existing_event["to_release_id"] != str(target_release_id)
                or existing_event["to_sha256"] != target_release_sha256
                or int(existing_event["previous_generation"]) != expected_active_generation
                or existing_event["from_release_id"] != str(expected_active_release_id)
                or existing_event["reason"] != reason
            ):
                raise ExpertMemoryPackageError("distribution_rollback_request_conflict")
            return _public_distribution_rollback_event(existing_event)
        row = connection.execute(
            """
            SELECT d.id AS distribution_id, d.state AS distribution_state,
                   d.rolled_back_at, d.release_id AS distributed_release_id,
                   active.release_id AS active_release_id, active.generation AS active_generation,
                   event.from_release_id AS parent_release_id,
                   current_release.package_sha256 AS current_sha256,
                   parent_release.package_sha256 AS parent_sha256
            FROM expert_memory_distributions AS d
            JOIN expert_active_release AS active ON active.singleton = 1
            JOIN expert_memory_distribution_events AS event
              ON event.distribution_id = d.id
             AND event.to_release_id = active.release_id
            JOIN expert_releases AS current_release
              ON current_release.id = active.release_id
            JOIN expert_releases AS parent_release
              ON parent_release.id = event.from_release_id
            WHERE d.id = ?
            ORDER BY event.generation DESC
            LIMIT 1
            """,
            (str(distribution_id),),
        ).fetchone()
        if row is None:
            raise ExpertMemoryPackageError("distribution_active_event_not_found")
        if row["distribution_state"] != "activated" or row["rolled_back_at"] is not None:
            raise ExpertMemoryPackageError("distribution_not_rollbackable")
        if row["active_release_id"] != str(expected_active_release_id):
            raise ExpertMemoryPackageError("active_release_conflict")
        if row["active_generation"] != expected_active_generation:
            raise ExpertMemoryPackageError("active_generation_conflict")
        if row["distributed_release_id"] != str(expected_active_release_id):
            raise ExpertMemoryPackageError("distribution_active_release_conflict")
        if row["parent_release_id"] != str(target_release_id):
            raise ExpertMemoryPackageError("rollback_target_conflict")
        if row["parent_sha256"] != target_release_sha256:
            raise ExpertMemoryPackageError("rollback_target_hash_mismatch")
        next_generation = expected_active_generation + 1
        connection.execute(
            "UPDATE expert_releases SET state = 'retired' WHERE id = ? AND state = 'eligible'",
            (row["distributed_release_id"],),
        )
        if (
            connection.execute(
                "UPDATE expert_releases SET state = 'eligible' WHERE id = ? AND state = 'retired'",
                (row["parent_release_id"],),
            ).rowcount
            != 1
        ):
            raise ExpertMemoryPackageError("rollback_target_state_conflict")
        if (
            connection.execute(
                """
                UPDATE expert_active_release
                SET release_id = ?, generation = ?, updated_at = ?
                WHERE singleton = 1 AND generation = ? AND release_id = ?
                """,
                (
                    row["parent_release_id"],
                    next_generation,
                    timestamp,
                    expected_active_generation,
                    row["distributed_release_id"],
                ),
            ).rowcount
            != 1
        ):
            raise ExpertMemoryPackageError("active_generation_conflict")
        connection.execute(
            """
            UPDATE expert_memory_distributions
            SET rolled_back_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (timestamp, timestamp, str(distribution_id)),
        )
        event_id = uuid4()
        connection.execute(
            """
            INSERT INTO expert_memory_distribution_rollback_events(
                id, distribution_id, from_release_id, to_release_id,
                previous_generation, generation, client_request_id,
                from_sha256, to_sha256, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(event_id),
                str(distribution_id),
                row["distributed_release_id"],
                row["parent_release_id"],
                expected_active_generation,
                next_generation,
                str(client_request_id),
                row["current_sha256"],
                row["parent_sha256"],
                reason,
                timestamp,
            ),
        )
        event = connection.execute(
            "SELECT * FROM expert_memory_distribution_rollback_events WHERE id = ?",
            (str(event_id),),
        ).fetchone()
    if event is None:
        raise ExpertMemoryPackageError("distribution_rollback_disappeared")
    return _public_distribution_rollback_event(event)


def _load_distribution(connection, distribution_id: UUID):
    return connection.execute(
        "SELECT * FROM expert_memory_distributions WHERE id = ?",
        (str(distribution_id),),
    ).fetchone()


def _public_distribution(row) -> dict[str, object]:
    return {
        "id": UUID(str(row["id"])),
        "release_id": UUID(str(row["release_id"])),
        "package_sha256": row["package_sha256"],
        "package_manifest_sha256": row["package_manifest_sha256"],
        "approved_review_sha256": row["approved_review_sha256"],
        "state": "rolled_back" if row["rolled_back_at"] is not None else row["state"],
        "reviewer_label": row["reviewer_label"],
        "reason": row["reason"],
        "created_at": datetime.fromisoformat(row["created_at"]),
        "updated_at": datetime.fromisoformat(row["updated_at"]),
    }


def _public_distribution_event(row) -> dict[str, object]:
    return {
        "id": UUID(str(row["id"])),
        "distribution_id": UUID(str(row["distribution_id"])),
        "from_release_id": UUID(str(row["from_release_id"]))
        if row["from_release_id"] is not None
        else None,
        "to_release_id": UUID(str(row["to_release_id"])),
        "previous_generation": int(row["previous_generation"]),
        "generation": int(row["generation"]),
        "client_request_id": UUID(str(row["client_request_id"])),
        "package_sha256": row["package_sha256"],
        "created_at": datetime.fromisoformat(row["created_at"]),
    }


def _public_distribution_rollback_event(row) -> dict[str, object]:
    return {
        "id": UUID(str(row["id"])),
        "distribution_id": UUID(str(row["distribution_id"])),
        "from_release_id": UUID(str(row["from_release_id"])),
        "to_release_id": UUID(str(row["to_release_id"])),
        "previous_generation": int(row["previous_generation"]),
        "generation": int(row["generation"]),
        "client_request_id": UUID(str(row["client_request_id"])),
        "from_sha256": row["from_sha256"],
        "to_sha256": row["to_sha256"],
        "reason": row["reason"],
        "created_at": datetime.fromisoformat(row["created_at"]),
    }


def _verify_signature(root: Path, allowed_signers: str | Path) -> ExpertMemorySignature:
    manifest_path = root / EXPERT_MEMORY_SIGNATURE_MANIFEST
    source = root / EXPERT_MEMORY_FILENAME
    signature = root / EXPERT_MEMORY_SIGNATURE_FILENAME
    try:
        manifest = ExpertMemorySignature.model_validate_json(manifest_path.read_bytes())
    except (OSError, ValueError) as error:
        raise ExpertMemoryPackageError("invalid_signature_manifest") from error
    if (
        not signature.is_file()
        or manifest.artifact_sha256 != sha256_file(source)
        or not Path(allowed_signers).is_file()
    ):
        raise ExpertMemoryPackageError("signature_artifact_mismatch")
    with source.open("rb") as stream:
        result = subprocess.run(
            [
                _ssh_keygen(),
                "-Y",
                "verify",
                "-f",
                str(Path(allowed_signers).resolve()),
                "-I",
                manifest.signer_identity,
                "-n",
                manifest.namespace,
                "-s",
                str(signature),
            ],
            stdin=stream,
            capture_output=True,
        )
    if result.returncode != 0:
        raise ExpertMemoryPackageError("signature_verification_failed")
    return manifest
