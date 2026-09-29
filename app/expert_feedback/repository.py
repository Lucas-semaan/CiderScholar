"""Persistence and server-side validation for private expert corrections."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.database.sqlite import Database
from app.expert_feedback.models import ExpertCorrectionCreate, ExpertCorrectionUpdate
from app.knowledge.trace import ExpertRunManifest


class ExpertCorrectionConflictError(ValueError):
    """The idempotency key was already used with another payload."""


class ExpertCorrectionValidationError(ValueError):
    """The message, trace, claim, or evidence reference is not admissible."""


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _response_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class ExpertCorrectionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def submit(
        self,
        message_id: UUID,
        payload: ExpertCorrectionCreate,
        *,
        now: datetime | None = None,
    ) -> tuple[dict[str, object], bool]:
        submitted_at = now or datetime.now(UTC)
        with self.database.transaction() as connection:
            message = _validate_message(connection, message_id)
            manifest_id = payload.manifest_id or _succeeded_manifest_id(connection, message_id)
            if manifest_id != payload.manifest_id:
                payload = payload.model_copy(update={"manifest_id": manifest_id})
            payload_json = payload.model_dump_json()
            existing = connection.execute(
                """
                SELECT id, message_id, manifest_id, status, revision, created_at, updated_at,
                       payload_json
                FROM expert_corrections
                WHERE message_id = ? AND client_request_id = ?
                """,
                (str(message_id), str(payload.client_request_id)),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != payload_json:
                    raise ExpertCorrectionConflictError("client request id was reused")
                return _public_row(existing), False

            status = "diagnosis_incomplete" if manifest_id is None else "submitted"
            _validate_payload_references(connection, message_id, message["content"], payload)
            correction_id = uuid4()
            timestamp = _timestamp(submitted_at)
            connection.execute(
                """
                INSERT INTO expert_corrections(
                    id, message_id, manifest_id, client_request_id, payload_json,
                    status, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    str(correction_id),
                    str(message_id),
                    str(manifest_id) if manifest_id is not None else None,
                    str(payload.client_request_id),
                    payload_json,
                    status,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO expert_correction_revisions(
                    id, correction_id, client_request_id, revision, payload_json, status, created_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(correction_id),
                    str(payload.client_request_id),
                    payload_json,
                    status,
                    timestamp,
                ),
            )
        return {
            "id": correction_id,
            "message_id": message_id,
            "manifest_id": manifest_id,
            "status": status,
            "revision": 1,
            "payload": payload.model_dump(mode="json"),
            "created_at": submitted_at,
            "updated_at": submitted_at,
        }, True

    def get(self, correction_id: UUID) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT id, message_id, manifest_id, status, revision, created_at, updated_at,
                       payload_json
                FROM expert_corrections WHERE id = ?
                """,
                (str(correction_id),),
            ).fetchone()
        return _public_row(row) if row is not None else None

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 100:
            raise ValueError("correction list limit must be between 1 and 100")
        parameters: list[object] = []
        clauses: list[str] = []
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        if cursor:
            try:
                cursor_created_at, cursor_id = cursor.split("|", 1)
            except ValueError as error:
                raise ValueError("correction cursor is invalid") from error
            clauses.append("(created_at < ? OR (created_at = ? AND id < ?))")
            parameters.extend((cursor_created_at, cursor_created_at, cursor_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, message_id, manifest_id, status, revision, created_at, updated_at,
                       payload_json
                FROM expert_corrections {where}
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [_public_row(row) for row in rows]

    def update(
        self,
        correction_id: UUID,
        payload: ExpertCorrectionUpdate,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Apply a correction only against its expected revision to prevent lost reviews."""

        updated_at = now or datetime.now(UTC)
        request_json = payload.model_dump_json()
        replacement = ExpertCorrectionCreate.model_validate(
            payload.model_dump(exclude={"expected_revision", "withdrawal"})
        )
        with self.database.transaction() as connection:
            current = connection.execute(
                """
                SELECT id, message_id, manifest_id, status, revision, created_at, updated_at,
                       payload_json
                FROM expert_corrections WHERE id = ?
                """,
                (str(correction_id),),
            ).fetchone()
            if current is None:
                raise ExpertCorrectionValidationError("correction does not exist")
            existing_revision = connection.execute(
                """
                SELECT payload_json FROM expert_correction_revisions
                WHERE correction_id = ? AND client_request_id = ?
                """,
                (str(correction_id), str(payload.client_request_id)),
            ).fetchone()
            if existing_revision is not None:
                if existing_revision["payload_json"] != request_json:
                    raise ExpertCorrectionConflictError("client request id was reused")
                return _public_row(current)
            if int(current["revision"]) != payload.expected_revision:
                raise ExpertCorrectionConflictError("correction revision has changed")
            message = _validate_message(connection, UUID(str(current["message_id"])))
            manifest_id = replacement.manifest_id or (
                UUID(str(current["manifest_id"])) if current["manifest_id"] else None
            )
            if manifest_id is None:
                manifest_id = _succeeded_manifest_id(connection, UUID(str(current["message_id"])))
            if manifest_id != replacement.manifest_id:
                replacement = replacement.model_copy(update={"manifest_id": manifest_id})
            _validate_payload_references(
                connection,
                UUID(str(current["message_id"])),
                message["content"],
                replacement,
            )
            next_revision = payload.expected_revision + 1
            next_status = (
                "withdrawn"
                if payload.withdrawal
                else ("diagnosis_incomplete" if replacement.manifest_id is None else "submitted")
            )
            timestamp = _timestamp(updated_at)
            connection.execute(
                """
                INSERT INTO expert_correction_revisions(
                    id, correction_id, client_request_id, revision, payload_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(correction_id),
                    str(payload.client_request_id),
                    next_revision,
                    request_json,
                    next_status,
                    timestamp,
                ),
            )
            connection.execute(
                """
                UPDATE expert_corrections
                SET manifest_id = ?, payload_json = ?, status = ?, revision = ?, updated_at = ?
                WHERE id = ? AND revision = ?
                """,
                (
                    str(replacement.manifest_id) if replacement.manifest_id else None,
                    replacement.model_dump_json(),
                    next_status,
                    next_revision,
                    timestamp,
                    str(correction_id),
                    payload.expected_revision,
                ),
            )
            updated = connection.execute(
                """
                SELECT id, message_id, manifest_id, status, revision, created_at, updated_at,
                       payload_json
                FROM expert_corrections WHERE id = ?
                """,
                (str(correction_id),),
            ).fetchone()
        return _public_row(updated)


def _reference_key(reference: object) -> tuple[str, str, str]:
    data = reference.model_dump(mode="python")
    kind = str(data["kind"])
    source_id = str(data.get("chunk_id") or data.get("record_id") or data.get("article_id"))
    if kind == "chunk":
        source_id = str(data["chunk_id"])
    return kind, source_id, str(data["content_sha256"])


def _validate_message(connection: sqlite3.Connection, message_id: UUID) -> sqlite3.Row:
    message = connection.execute(
        "SELECT role, content FROM chat_messages WHERE id = ?",
        (str(message_id),),
    ).fetchone()
    if message is None:
        raise ExpertCorrectionValidationError("assistant message does not exist")
    if message["role"] != "assistant":
        raise ExpertCorrectionValidationError("expert corrections require an assistant message")
    return message


def _succeeded_manifest_id(connection: sqlite3.Connection, message_id: UUID) -> UUID | None:
    row = connection.execute(
        """
        SELECT id FROM expert_run_manifests
        WHERE result_message_id = ? AND state = 'succeeded'
        ORDER BY updated_at DESC, id DESC LIMIT 1
        """,
        (str(message_id),),
    ).fetchone()
    return UUID(str(row["id"])) if row is not None else None


def _validate_payload_references(
    connection: sqlite3.Connection,
    message_id: UUID,
    message_content: str,
    payload: ExpertCorrectionCreate,
) -> None:
    if payload.manifest_id is not None:
        manifest_row = connection.execute(
            "SELECT payload_json, result_message_id FROM expert_run_manifests WHERE id = ?",
            (str(payload.manifest_id),),
        ).fetchone()
        if manifest_row is None or manifest_row["result_message_id"] != str(message_id):
            raise ExpertCorrectionValidationError("manifest is not attached to this message")
        manifest = ExpertRunManifest.model_validate_json(manifest_row["payload_json"])
        if manifest.output.response_sha256 != _response_hash(message_content):
            raise ExpertCorrectionValidationError(
                "manifest response hash does not match the message"
            )
        if payload.claim_id is not None and payload.claim_id not in {
            link.claim_id for link in manifest.output.claim_links
        }:
            raise ExpertCorrectionValidationError("claim is absent from the manifest")
        known_evidence = {
            (item.source_kind, item.source_id, item.text_sha256) for item in manifest.evidence
        }
        if any(
            _reference_key(reference) not in known_evidence for reference in payload.evidence_refs
        ):
            raise ExpertCorrectionValidationError("evidence reference is absent from the manifest")
    if payload.selected_text and payload.selected_text not in message_content:
        raise ExpertCorrectionValidationError("selected text is absent from the message")


def _public_row(row: object) -> dict[str, object]:
    data = dict(row)  # type: ignore[arg-type]
    payload = ExpertCorrectionCreate.model_validate_json(data.pop("payload_json"))
    data["payload"] = payload.model_dump(mode="json")
    data["id"] = UUID(str(data["id"]))
    data["message_id"] = UUID(str(data["message_id"]))
    data["manifest_id"] = UUID(str(data["manifest_id"])) if data["manifest_id"] else None
    return data
