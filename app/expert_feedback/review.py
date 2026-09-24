"""Explicit human review and promotion gates for isolated expert candidates."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.database.sqlite import Database
from app.expert_feedback.models import (
    ExpertActivationRequest,
    ExpertReviewCreate,
    ExpertRollbackRequest,
)
from app.knowledge.contracts import content_hash


class ExpertReviewError(ValueError):
    """Stable error codes for review and activation transitions."""


class ExpertReviewConflictError(RuntimeError):
    """An idempotency key was reused with a different review or activation."""


def _activation_row(
    connection: sqlite3.Connection, candidate_id: UUID, review_id: UUID
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT c.state AS candidate_state, c.base_release_id, c.candidate_release_id,
               candidate_release.state AS release_state,
               candidate_release.package_sha256,
               review.candidate_sha256, review.evaluation_sha256,
               review.decision, evaluation.state AS evaluation_state,
               evaluation.report_sha256,
               active.release_id AS active_release_id,
               active.generation AS active_generation
        FROM expert_candidates AS c
        JOIN expert_releases AS candidate_release
          ON candidate_release.id = c.candidate_release_id
        JOIN expert_reviews AS review
          ON review.id = ? AND review.candidate_id = c.id
        JOIN expert_evaluations AS evaluation
          ON evaluation.id = review.evaluation_id
        JOIN expert_active_release AS active ON active.singleton = 1
        WHERE c.id = ?
        """,
        (str(review_id), str(candidate_id)),
    ).fetchone()


def _validate_activation_row(row: sqlite3.Row | None, request: ExpertActivationRequest) -> None:
    if row is None:
        raise ExpertReviewError("candidate_or_review_not_found")
    if row["candidate_state"] != "approved" or row["release_state"] != "candidate":
        raise ExpertReviewError("candidate_not_approved")
    if row["decision"] != "approve":
        raise ExpertReviewError("review_does_not_approve")
    if row["evaluation_state"] != "passed":
        raise ExpertReviewError("evaluation_not_passed")
    if row["package_sha256"] != request.candidate_sha256:
        raise ExpertReviewError("candidate_hash_mismatch")
    if row["candidate_sha256"] != request.candidate_sha256:
        raise ExpertReviewError("review_candidate_hash_mismatch")
    if row["report_sha256"] != request.evaluation_sha256:
        raise ExpertReviewError("evaluation_hash_mismatch")
    if row["evaluation_sha256"] != request.evaluation_sha256:
        raise ExpertReviewError("review_evaluation_hash_mismatch")
    if row["active_generation"] != request.expected_active_generation:
        raise ExpertReviewError("active_generation_conflict")
    if row["active_release_id"] != row["base_release_id"]:
        raise ExpertReviewError("candidate_base_is_not_active")


def preflight_activation(
    database: Database,
    candidate_id: UUID,
    request: ExpertActivationRequest,
) -> dict[str, object]:
    """Validate every activation gate without changing state."""

    with database.connect() as connection:
        row = _activation_row(connection, candidate_id, request.review_id)
    _validate_activation_row(row, request)
    assert row is not None
    return {
        "candidate_id": candidate_id,
        "review_id": request.review_id,
        "candidate_sha256": request.candidate_sha256,
        "evaluation_sha256": request.evaluation_sha256,
        "active_release_id": UUID(str(row["active_release_id"]))
        if row["active_release_id"] is not None
        else None,
        "active_generation": int(row["active_generation"]),
        "would_activate_release_id": UUID(str(row["candidate_release_id"])),
    }


def create_review(
    database: Database,
    candidate_id: UUID,
    review: ExpertReviewCreate,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, object], bool]:
    """Record a human decision only for the exact evaluated candidate and report."""

    timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    with database.transaction() as connection:
        existing = connection.execute(
            """
            SELECT * FROM expert_reviews
            WHERE candidate_id = ? AND client_request_id = ?
            """,
            (str(candidate_id), str(review.client_request_id)),
        ).fetchone()
        if existing is not None:
            if _review_fingerprint(existing) != content_hash(review.model_dump(mode="json")):
                raise ExpertReviewConflictError("review client request id was reused")
            return _public_review(existing), False
        row = connection.execute(
            """
            SELECT c.state AS candidate_state, r.package_sha256,
                   e.id AS evaluation_id, e.report_sha256, e.state AS evaluation_state
            FROM expert_candidates AS c
            JOIN expert_releases AS r ON r.id = c.candidate_release_id
            JOIN expert_evaluations AS e ON e.candidate_id = c.id
            WHERE c.id = ? AND e.report_sha256 = ?
            ORDER BY e.created_at DESC
            LIMIT 1
            """,
            (str(candidate_id), review.evaluation_sha256),
        ).fetchone()
        if row is None:
            raise ExpertReviewError("candidate_or_evaluation_not_found")
        if row["candidate_state"] != "awaiting_review":
            raise ExpertReviewError("candidate_not_awaiting_review")
        if row["package_sha256"] != review.candidate_sha256:
            raise ExpertReviewError("candidate_hash_mismatch")
        if row["evaluation_state"] != "passed":
            raise ExpertReviewError("evaluation_not_passed")
        review_id = uuid4()
        connection.execute(
            """
            INSERT INTO expert_reviews(
                id, candidate_id, evaluation_id, client_request_id,
                candidate_sha256, evaluation_sha256, decision,
                reviewer_label, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(review_id),
                str(candidate_id),
                str(row["evaluation_id"]),
                str(review.client_request_id),
                review.candidate_sha256,
                review.evaluation_sha256,
                review.decision,
                review.reviewer_label,
                review.reason,
                timestamp,
            ),
        )
        next_state = {
            "approve": "approved",
            "reject": "rejected",
            "needs_changes": "needs_expert",
        }[review.decision]
        connection.execute(
            "UPDATE expert_candidates SET state = ?, updated_at = ? WHERE id = ?",
            (next_state, timestamp, str(candidate_id)),
        )
        stored = connection.execute(
            "SELECT * FROM expert_reviews WHERE id = ?", (str(review_id),)
        ).fetchone()
    if stored is None:
        raise ExpertReviewError("review_disappeared")
    return _public_review(stored), True


def get_review(database: Database, review_id: UUID) -> dict[str, object] | None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM expert_reviews WHERE id = ?", (str(review_id),)
        ).fetchone()
    return _public_review(row) if row is not None else None


def activate_candidate(
    database: Database,
    candidate_id: UUID,
    request: ExpertActivationRequest,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Atomically promote an approved candidate after a matching human review."""

    timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    with database.transaction() as connection:
        existing = connection.execute(
            """
            SELECT * FROM expert_activation_events
            WHERE candidate_id = ? AND client_request_id = ?
            """,
            (str(candidate_id), str(request.client_request_id)),
        ).fetchone()
        if existing is not None:
            if (
                existing["review_id"] != str(request.review_id)
                or existing["candidate_sha256"] != request.candidate_sha256
                or existing["evaluation_sha256"] != request.evaluation_sha256
                or int(existing["previous_generation"]) != request.expected_active_generation
            ):
                raise ExpertReviewConflictError("activation client request id was reused")
            return _public_activation(existing)
        row = _activation_row(connection, candidate_id, request.review_id)
        _validate_activation_row(row, request)
        assert row is not None
        next_generation = int(row["active_generation"]) + 1
        activation_id = uuid4()
        if row["active_release_id"] is not None:
            connection.execute(
                "UPDATE expert_releases SET state = 'retired' WHERE id = ? AND state = 'eligible'",
                (row["active_release_id"],),
            )
        release_cursor = connection.execute(
            "UPDATE expert_releases SET state = 'eligible' WHERE id = ? AND state = 'candidate'",
            (row["candidate_release_id"],),
        )
        if release_cursor.rowcount != 1:
            raise ExpertReviewError("candidate_release_state_conflict")
        active_cursor = connection.execute(
            """
            UPDATE expert_active_release
            SET release_id = ?, generation = ?, updated_at = ?
            WHERE singleton = 1 AND generation = ?
            """,
            (
                row["candidate_release_id"],
                next_generation,
                timestamp,
                row["active_generation"],
            ),
        )
        if active_cursor.rowcount != 1:
            raise ExpertReviewError("active_generation_conflict")
        connection.execute(
            "UPDATE expert_candidates SET state = 'activated', updated_at = ? WHERE id = ?",
            (timestamp, str(candidate_id)),
        )
        connection.execute(
            """
            INSERT INTO expert_activation_events(
                id, candidate_id, review_id, from_release_id, to_release_id,
                previous_generation, generation, client_request_id,
                candidate_sha256, evaluation_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(activation_id),
                str(candidate_id),
                str(request.review_id),
                row["active_release_id"],
                row["candidate_release_id"],
                row["active_generation"],
                next_generation,
                str(request.client_request_id),
                request.candidate_sha256,
                request.evaluation_sha256,
                timestamp,
            ),
        )
        stored = connection.execute(
            "SELECT * FROM expert_activation_events WHERE id = ?", (str(activation_id),)
        ).fetchone()
    if stored is None:
        raise ExpertReviewError("activation_disappeared")
    return _public_activation(stored)


def _rollback_row(connection: sqlite3.Connection, target_release_id: UUID) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT active.release_id AS active_release_id, active.generation AS active_generation,
               current_release.package_sha256 AS active_sha256,
               c.id AS candidate_id, c.base_release_id,
               c.state AS candidate_state,
               target.state AS target_state, target.package_sha256 AS target_sha256
        FROM expert_active_release AS active
        JOIN expert_releases AS current_release
          ON current_release.id = active.release_id
        JOIN expert_candidates AS c
          ON c.candidate_release_id = active.release_id
        JOIN expert_releases AS target ON target.id = c.base_release_id
        WHERE active.singleton = 1 AND target.id = ?
        """,
        (str(target_release_id),),
    ).fetchone()


def _validate_rollback_row(row: sqlite3.Row | None, request: ExpertRollbackRequest) -> None:
    if row is None:
        raise ExpertReviewError("rollback_target_is_not_active_parent")
    if row["active_generation"] != request.expected_active_generation:
        raise ExpertReviewError("active_generation_conflict")
    if row["candidate_state"] != "activated":
        raise ExpertReviewError("active_release_is_not_expert_candidate")
    if row["target_sha256"] != request.target_release_sha256:
        raise ExpertReviewError("rollback_target_hash_mismatch")
    if row["target_state"] not in {"retired", "eligible"}:
        raise ExpertReviewError("rollback_target_not_restorable")


def preflight_rollback(database: Database, request: ExpertRollbackRequest) -> dict[str, object]:
    """Validate every rollback gate without changing state."""

    with database.connect() as connection:
        row = _rollback_row(connection, request.target_release_id)
    _validate_rollback_row(row, request)
    assert row is not None
    return {
        "target_release_id": request.target_release_id,
        "target_release_sha256": request.target_release_sha256,
        "active_release_id": UUID(str(row["active_release_id"])),
        "active_generation": int(row["active_generation"]),
        "candidate_id": UUID(str(row["candidate_id"])),
        "would_activate_generation": int(row["active_generation"]) + 1,
    }


def rollback_active_candidate(
    database: Database,
    request: ExpertRollbackRequest,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Restore only the active candidate's immediate parent release."""

    timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    with database.transaction() as connection:
        existing = connection.execute(
            "SELECT * FROM expert_rollback_events WHERE client_request_id = ?",
            (str(request.client_request_id),),
        ).fetchone()
        if existing is not None:
            if (
                existing["to_release_id"] != str(request.target_release_id)
                or existing["to_sha256"] != request.target_release_sha256
                or int(existing["previous_generation"]) != request.expected_active_generation
                or existing["reason"] != request.reason
            ):
                raise ExpertReviewConflictError("rollback client request id was reused")
            return _public_rollback(existing)
        row = _rollback_row(connection, request.target_release_id)
        _validate_rollback_row(row, request)
        assert row is not None
        next_generation = int(row["active_generation"]) + 1
        connection.execute(
            "UPDATE expert_releases SET state = 'retired' WHERE id = ? AND state = 'eligible'",
            (row["active_release_id"],),
        )
        target_cursor = connection.execute(
            "UPDATE expert_releases SET state = 'eligible' "
            "WHERE id = ? AND state IN ('retired', 'eligible')",
            (str(request.target_release_id),),
        )
        if target_cursor.rowcount != 1:
            raise ExpertReviewError("rollback_target_state_conflict")
        active_cursor = connection.execute(
            """
            UPDATE expert_active_release
            SET release_id = ?, generation = ?, updated_at = ?
            WHERE singleton = 1 AND generation = ?
            """,
            (
                str(request.target_release_id),
                next_generation,
                timestamp,
                request.expected_active_generation,
            ),
        )
        if active_cursor.rowcount != 1:
            raise ExpertReviewError("active_generation_conflict")
        connection.execute(
            "UPDATE expert_candidates SET state = 'superseded', updated_at = ? WHERE id = ?",
            (timestamp, row["candidate_id"]),
        )
        rollback_id = uuid4()
        connection.execute(
            """
            INSERT INTO expert_rollback_events(
                id, from_release_id, to_release_id, candidate_id, client_request_id,
                from_sha256, to_sha256, previous_generation, generation, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(rollback_id),
                row["active_release_id"],
                str(request.target_release_id),
                row["candidate_id"],
                str(request.client_request_id),
                row["active_sha256"],
                request.target_release_sha256,
                request.expected_active_generation,
                next_generation,
                request.reason,
                timestamp,
            ),
        )
        stored = connection.execute(
            "SELECT * FROM expert_rollback_events WHERE id = ?", (str(rollback_id),)
        ).fetchone()
    if stored is None:
        raise ExpertReviewError("rollback_disappeared")
    return _public_rollback(stored)


def _review_fingerprint(row) -> str:
    return content_hash(
        {
            "client_request_id": row["client_request_id"],
            "candidate_sha256": row["candidate_sha256"],
            "evaluation_sha256": row["evaluation_sha256"],
            "decision": row["decision"],
            "reviewer_label": row["reviewer_label"],
            "reason": row["reason"],
        }
    )


def _public_review(row) -> dict[str, object]:
    return {
        "id": UUID(str(row["id"])),
        "candidate_id": UUID(str(row["candidate_id"])),
        "evaluation_id": UUID(str(row["evaluation_id"])),
        "client_request_id": UUID(str(row["client_request_id"])),
        "candidate_sha256": row["candidate_sha256"],
        "evaluation_sha256": row["evaluation_sha256"],
        "decision": row["decision"],
        "reviewer_label": row["reviewer_label"],
        "reason": row["reason"],
        "created_at": datetime.fromisoformat(row["created_at"]),
    }


def _public_activation(row) -> dict[str, object]:
    return {
        "id": UUID(str(row["id"])),
        "candidate_id": UUID(str(row["candidate_id"])),
        "review_id": UUID(str(row["review_id"])),
        "from_release_id": UUID(str(row["from_release_id"]))
        if row["from_release_id"] is not None
        else None,
        "to_release_id": UUID(str(row["to_release_id"])),
        "previous_generation": int(row["previous_generation"]),
        "generation": int(row["generation"]),
        "client_request_id": UUID(str(row["client_request_id"])),
        "candidate_sha256": row["candidate_sha256"],
        "evaluation_sha256": row["evaluation_sha256"],
        "created_at": datetime.fromisoformat(row["created_at"]),
    }


def _public_rollback(row) -> dict[str, object]:
    return {
        "id": UUID(str(row["id"])),
        "from_release_id": UUID(str(row["from_release_id"])),
        "to_release_id": UUID(str(row["to_release_id"])),
        "candidate_id": UUID(str(row["candidate_id"])),
        "client_request_id": UUID(str(row["client_request_id"])),
        "from_sha256": row["from_sha256"],
        "to_sha256": row["to_sha256"],
        "previous_generation": int(row["previous_generation"]),
        "generation": int(row["generation"]),
        "reason": row["reason"],
        "created_at": datetime.fromisoformat(row["created_at"]),
    }
