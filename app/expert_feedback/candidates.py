"""Bounded, immutable compilation of expert diagnoses into candidate releases."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.database.sqlite import Database
from app.expert_feedback.models import CandidatePatch, Diagnosis, PatchOperation
from app.knowledge.contracts import content_hash
from app.knowledge.models import KnowledgePackage
from app.knowledge.repository import KnowledgeRepository, KnowledgeRepositoryError


class ExpertCandidateError(ValueError):
    """Stable candidate-compilation validation error."""


_EVALUATION_START_STATE = "structurally_valid"


class ExpertCandidateCompiler:
    """Compile only approved-shape, deterministic patches into isolated releases."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.knowledge = KnowledgeRepository(database)

    def compile(
        self,
        patch: CandidatePatch,
        *,
        diagnosis_id: UUID,
        now: datetime | None = None,
    ) -> tuple[dict[str, object], bool]:
        """Bind a proposed patch to its reviewed diagnosis and content hashes before evaluation."""

        timestamp = now or datetime.now(UTC)
        diff_sha256 = content_hash(patch.model_dump(mode="json"))
        diagnosis, diagnosis_payload = self._load_diagnosis(diagnosis_id)
        if diagnosis["diagnosis_sha256"] != patch.diagnosis_sha256:
            raise ExpertCandidateError("diagnosis_hash_mismatch")
        if diagnosis_payload.confidence != "supported":
            raise ExpertCandidateError("diagnosis_not_supported")
        if diagnosis_payload.proposed_action != "knowledge_candidate":
            raise ExpertCandidateError("diagnosis_target_not_compilable")
        if not set(operation.item_id for operation in patch.operations) <= set(
            diagnosis_payload.target_item_ids
        ):
            raise ExpertCandidateError("patch_target_not_authorized")

        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT id, diagnosis_id, base_release_id, candidate_release_id,
                       diff_sha256, state, attempt, created_at, updated_at
                FROM expert_candidates
                WHERE diagnosis_id = ? AND diff_sha256 = ?
                """,
                (str(diagnosis_id), diff_sha256),
            ).fetchone()
            if existing is not None:
                return _public_candidate(existing), False

        package = self._build_package(patch)
        try:
            release = self.knowledge.import_candidate(
                package, base_release_id=patch.base_release_id
            )
        except KnowledgeRepositoryError as error:
            raise ExpertCandidateError(str(error)) from error
        candidate_id = uuid4()
        created_at = timestamp.astimezone(UTC).isoformat()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO expert_candidates(
                        id, diagnosis_id, base_release_id, candidate_release_id,
                        diff_sha256, state, attempt, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'structurally_valid', 0, ?, ?)
                    """,
                    (
                        str(candidate_id),
                        str(diagnosis_id),
                        str(patch.base_release_id),
                        str(release.id),
                        diff_sha256,
                        created_at,
                        created_at,
                    ),
                )
                connection.execute(
                    """
                    UPDATE expert_corrections
                    SET status = 'candidate_ready', updated_at = ?
                    WHERE id = (
                        SELECT correction_id FROM expert_diagnoses WHERE id = ?
                    ) AND status IN (
                        'submitted', 'diagnosis_incomplete', 'diagnosed', 'needs_expert',
                        'candidate_ready'
                    )
                    """,
                    (created_at, str(diagnosis_id)),
                )
                row = connection.execute(
                    """
                    SELECT id, diagnosis_id, base_release_id, candidate_release_id,
                           diff_sha256, state, attempt, created_at, updated_at
                    FROM expert_candidates WHERE id = ?
                    """,
                    (str(candidate_id),),
                ).fetchone()
        except sqlite3.IntegrityError as error:
            if "UNIQUE constraint failed" not in str(error):
                raise
            with self.database.connect() as connection:
                row = connection.execute(
                    """
                    SELECT id, diagnosis_id, base_release_id, candidate_release_id,
                           diff_sha256, state, attempt, created_at, updated_at
                    FROM expert_candidates
                    WHERE diagnosis_id = ? AND diff_sha256 = ?
                    """,
                    (str(diagnosis_id), diff_sha256),
                ).fetchone()
            if row is not None:
                return _public_candidate(row), False
            raise
        if row is None:
            raise ExpertCandidateError("candidate_disappeared")
        return _public_candidate(row), True

    def begin_evaluation(
        self, candidate_id: UUID, *, now: datetime | None = None
    ) -> dict[str, object]:
        """Move one structurally valid candidate into evaluation exactly once."""

        timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE expert_candidates
                SET state = 'evaluating', updated_at = ?
                WHERE id = ? AND state = ?
                """,
                (timestamp, str(candidate_id), _EVALUATION_START_STATE),
            )
            if cursor.rowcount != 1:
                row = connection.execute(
                    "SELECT state FROM expert_candidates WHERE id = ?",
                    (str(candidate_id),),
                ).fetchone()
                if row is None:
                    raise ExpertCandidateError("candidate_not_found")
                raise ExpertCandidateError("candidate_state_conflict")
            row = connection.execute(
                """
                SELECT id, diagnosis_id, base_release_id, candidate_release_id,
                       diff_sha256, state, attempt, created_at, updated_at
                FROM expert_candidates WHERE id = ?
                """,
                (str(candidate_id),),
            ).fetchone()
        if row is None:
            raise ExpertCandidateError("candidate_disappeared")
        return _public_candidate(row)

    def get(self, candidate_id: UUID) -> dict[str, object] | None:
        """Read candidate metadata and its bounded package without exposing conversations."""

        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT id, diagnosis_id, base_release_id, candidate_release_id,
                       diff_sha256, state, attempt, created_at, updated_at
                FROM expert_candidates WHERE id = ?
                """,
                (str(candidate_id),),
            ).fetchone()
        if row is None:
            return None
        result = _public_candidate(row)
        try:
            _stored, package = self.knowledge.load_release(UUID(str(row["candidate_release_id"])))
        except KnowledgeRepositoryError as error:
            raise ExpertCandidateError(str(error)) from error
        result["items"] = [item.model_dump(mode="json") for item in package.items]
        return result

    def _load_diagnosis(self, diagnosis_id: UUID):
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT expert_diagnoses.id, expert_diagnoses.correction_id,
                       expert_diagnoses.correction_revision, expert_diagnoses.payload_json,
                       expert_diagnoses.diagnosis_sha256, expert_diagnoses.created_at,
                       c.revision AS correction_current_revision, c.status AS correction_status
                FROM expert_diagnoses
                JOIN expert_corrections AS c ON c.id = expert_diagnoses.correction_id
                WHERE expert_diagnoses.id = ?
                """,
                (str(diagnosis_id),),
            ).fetchone()
        if row is None:
            raise ExpertCandidateError("diagnosis_not_found")
        if int(row["correction_current_revision"]) != int(row["correction_revision"]):
            raise ExpertCandidateError("diagnosis_stale")
        if row["correction_status"] == "withdrawn":
            raise ExpertCandidateError("correction_withdrawn")
        try:
            payload = Diagnosis.model_validate_json(row["payload_json"])
        except ValueError as error:
            raise ExpertCandidateError("diagnosis_invalid") from error
        return row, payload

    def _build_package(self, patch: CandidatePatch) -> KnowledgePackage:
        try:
            _stored, package = self.knowledge.load_release(patch.base_release_id)
        except KnowledgeRepositoryError as error:
            raise ExpertCandidateError(str(error)) from error
        items = {item.id: item for item in package.items}
        for operation in patch.operations:
            self._apply_operation(items, operation)
        if not items:
            raise ExpertCandidateError("candidate_package_empty")
        return package.model_copy(update={"items": tuple(items.values())})

    @staticmethod
    def _apply_operation(items: dict[str, object], operation: PatchOperation) -> None:
        current = items.get(operation.item_id)
        if operation.operation == "add_item":
            if current is not None:
                raise ExpertCandidateError("candidate_item_already_exists")
            assert operation.item is not None
            items[operation.item_id] = operation.item
            return
        if current is None:
            raise ExpertCandidateError("candidate_item_missing")
        if operation.expected_sha256 != current.content_sha256:
            raise ExpertCandidateError("candidate_base_item_hash_mismatch")
        if operation.operation == "retire_item":
            del items[operation.item_id]
            return
        assert operation.item is not None
        items[operation.item_id] = operation.item


def _public_candidate(row) -> dict[str, object]:
    return {
        "id": UUID(str(row["id"])),
        "diagnosis_id": UUID(str(row["diagnosis_id"])),
        "base_release_id": UUID(str(row["base_release_id"])),
        "candidate_release_id": UUID(str(row["candidate_release_id"])),
        "diff_sha256": row["diff_sha256"],
        "state": row["state"],
        "attempt": int(row["attempt"]),
        "created_at": datetime.fromisoformat(row["created_at"]),
        "updated_at": datetime.fromisoformat(row["updated_at"]),
    }
