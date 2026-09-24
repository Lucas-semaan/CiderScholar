"""Persistence boundary for the validation-only expert pilot."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.database.sqlite import Database
from app.evaluation.expert_memory import ExpertEvaluationManifest, ExpertEvaluationReport
from app.evaluation.expert_pilot import (
    ExpertPilotAttestation,
    ExpertPilotAudit,
    ExpertPilotError,
    ExpertPilotObservation,
    ExpertPilotPlan,
    audit_expert_pilot,
    build_expert_pilot_plan,
    build_pilot_metrics,
)


class ExpertPilotStoreError(ValueError):
    """The durable pilot record cannot be created or advanced safely."""


def create_pilot_plan(
    database: Database,
    evaluation_id: UUID,
    *,
    pilot_id: UUID,
    now: datetime | None = None,
) -> tuple[dict[str, object], bool]:
    """Create or return one plan bound to an existing immutable evaluation."""

    with database.transaction() as connection:
        row = connection.execute(
            """
            SELECT e.id AS evaluation_id, e.candidate_id, e.manifest_json,
                   c.state AS candidate_state, r.package_sha256 AS candidate_sha256
            FROM expert_evaluations AS e
            JOIN expert_candidates AS c ON c.id = e.candidate_id
            JOIN expert_releases AS r ON r.id = c.candidate_release_id
            WHERE e.id = ?
            """,
            (str(evaluation_id),),
        ).fetchone()
        if row is None:
            raise ExpertPilotStoreError("evaluation_not_found")
        manifest = ExpertEvaluationManifest.model_validate_json(row["manifest_json"])
        try:
            plan = build_expert_pilot_plan(
                manifest,
                pilot_id=pilot_id,
                evaluation_id=evaluation_id,
                candidate_sha256=row["candidate_sha256"],
            )
        except (ExpertPilotError, ValueError) as error:
            raise ExpertPilotStoreError(str(error)) from error
        existing = connection.execute(
            """
            SELECT id, candidate_id, evaluation_id, plan_json, plan_sha256,
                   audit_json, audit_sha256, attestation_json, attestation_sha256,
                   state, created_at, updated_at
            FROM expert_pilots WHERE id = ?
            """,
            (str(pilot_id),),
        ).fetchone()
        if existing is not None:
            if existing["plan_sha256"] != plan.plan_sha256:
                raise ExpertPilotStoreError("pilot_plan_conflict")
            return _public_pilot(existing, _load_observations(connection, pilot_id)), False
        timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO expert_pilots(
                id, candidate_id, evaluation_id, plan_json, plan_sha256,
                audit_json, audit_sha256, attestation_json, attestation_sha256,
                state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 'planned', ?, ?)
            """,
            (
                str(pilot_id),
                str(manifest.candidate_id),
                str(evaluation_id),
                plan.model_dump_json(),
                plan.plan_sha256,
                timestamp,
                timestamp,
            ),
        )
        saved = _load_pilot(connection, pilot_id)
        observations = _load_observations(connection, pilot_id)
    if saved is None:
        raise ExpertPilotStoreError("pilot_disappeared")
    return _public_pilot(saved, observations), True


def audit_pilot(
    database: Database,
    pilot_id: UUID,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Recompute readiness from the current candidate and immutable evaluation."""

    with database.transaction() as connection:
        row = connection.execute(
            """
            SELECT p.id, p.candidate_id, p.evaluation_id, p.plan_json, p.plan_sha256,
                   p.audit_json, p.audit_sha256, p.attestation_json, p.attestation_sha256,
                   p.state, p.created_at, p.updated_at,
                   c.state AS candidate_state, e.report_json
            FROM expert_pilots AS p
            JOIN expert_candidates AS c ON c.id = p.candidate_id
            JOIN expert_evaluations AS e ON e.id = p.evaluation_id
            WHERE p.id = ?
            """,
            (str(pilot_id),),
        ).fetchone()
        if row is None:
            raise ExpertPilotStoreError("pilot_not_found")
        plan = ExpertPilotPlan.model_validate_json(row["plan_json"])
        report = (
            ExpertEvaluationReport.model_validate_json(row["report_json"])
            if row["report_json"] is not None
            else None
        )
        audit = audit_expert_pilot(
            plan,
            candidate_state=row["candidate_state"],
            evaluation_id=UUID(str(row["evaluation_id"])),
            evaluation_report=report,
        )
        timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        connection.execute(
            """
            UPDATE expert_pilots
            SET audit_json = ?, audit_sha256 = ?, state = ?, updated_at = ?
            WHERE id = ? AND plan_sha256 = ?
            """,
            (
                audit.model_dump_json(),
                audit.audit_sha256,
                audit.state,
                timestamp,
                str(pilot_id),
                plan.plan_sha256,
            ),
        )
        saved = _load_pilot(connection, pilot_id)
        observations = _load_observations(connection, pilot_id)
    if saved is None:
        raise ExpertPilotStoreError("pilot_disappeared")
    return _public_pilot(saved, observations)


def attest_pilot(
    database: Database,
    pilot_id: UUID,
    attestation: ExpertPilotAttestation,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, object], bool]:
    """Persist one external attestation against the exact stored audit hash."""

    with database.transaction() as connection:
        row = _load_pilot(connection, pilot_id)
        if row is None:
            raise ExpertPilotStoreError("pilot_not_found")
        if row["audit_json"] is None or row["audit_sha256"] is None:
            raise ExpertPilotStoreError("pilot_audit_missing")
        if attestation.pilot_id != pilot_id or attestation.audit_sha256 != row["audit_sha256"]:
            raise ExpertPilotStoreError("pilot_attestation_hash_mismatch")
        audit = ExpertPilotAudit.model_validate_json(row["audit_json"])
        if audit.state != "ready" and attestation.decision == "accept":
            raise ExpertPilotStoreError("blocked pilot cannot be externally accepted")
        if row["attestation_json"] is not None:
            if row["attestation_sha256"] != attestation.attestation_sha256:
                raise ExpertPilotStoreError("pilot_attestation_conflict")
            return _public_pilot(row, _load_observations(connection, pilot_id)), False
        timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        connection.execute(
            """
            UPDATE expert_pilots
            SET attestation_json = ?, attestation_sha256 = ?, state = 'attested', updated_at = ?
            WHERE id = ? AND audit_sha256 = ? AND attestation_json IS NULL
            """,
            (
                attestation.model_dump_json(),
                attestation.attestation_sha256,
                timestamp,
                str(pilot_id),
                row["audit_sha256"],
            ),
        )
        saved = _load_pilot(connection, pilot_id)
        observations = _load_observations(connection, pilot_id)
    if saved is None:
        raise ExpertPilotStoreError("pilot_disappeared")
    return _public_pilot(saved, observations), True


def get_pilot(database: Database, pilot_id: UUID) -> dict[str, object] | None:
    with database.connect() as connection:
        row = _load_pilot(connection, pilot_id)
        observations = _load_observations(connection, pilot_id) if row is not None else ()
    return _public_pilot(row, observations) if row is not None else None


def record_pilot_observation(
    database: Database,
    pilot_id: UUID,
    observation: ExpertPilotObservation,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, object], bool]:
    """Append one bounded human observation and return deterministic aggregates."""

    with database.transaction() as connection:
        row = _load_pilot(connection, pilot_id)
        if row is None:
            raise ExpertPilotStoreError("pilot_not_found")
        if observation.pilot_id != pilot_id:
            raise ExpertPilotStoreError("pilot_observation_identity_mismatch")
        plan = ExpertPilotPlan.model_validate_json(row["plan_json"])
        if observation.protocol_sha256 != plan.measurement_protocol_sha256:
            raise ExpertPilotStoreError("pilot_measurement_protocol_mismatch")
        planned_cases = set(plan.question_hashes) | set(plan.control_question_hashes)
        if observation.case_sha256 not in planned_cases:
            raise ExpertPilotStoreError("pilot_case_not_in_plan")
        if row["state"] == "blocked":
            raise ExpertPilotStoreError("blocked_pilot_cannot_record_observation")
        if row["attestation_json"] is not None:
            attestation = ExpertPilotAttestation.model_validate_json(row["attestation_json"])
            if attestation.decision == "reject":
                raise ExpertPilotStoreError("rejected_pilot_cannot_record_observation")
        existing = connection.execute(
            """
            SELECT observation_json, observation_sha256
            FROM expert_pilot_observations
            WHERE pilot_id = ? AND case_sha256 = ?
            """,
            (str(pilot_id), observation.case_sha256),
        ).fetchone()
        if existing is not None:
            if existing["observation_sha256"] != observation.observation_sha256:
                raise ExpertPilotStoreError("pilot_observation_conflict")
            observations = _load_observations(connection, pilot_id)
            return _public_pilot(row, observations), False
        count = connection.execute(
            "SELECT COUNT(*) AS count FROM expert_pilot_observations WHERE pilot_id = ?",
            (str(pilot_id),),
        ).fetchone()["count"]
        if count >= 20:
            raise ExpertPilotStoreError("pilot observation limit exceeded")
        timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO expert_pilot_observations(
                id, pilot_id, case_sha256, protocol_sha256,
                observation_json, observation_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(observation.observation_id),
                str(pilot_id),
                observation.case_sha256,
                observation.protocol_sha256,
                observation.model_dump_json(),
                observation.observation_sha256,
                timestamp,
            ),
        )
        observations = _load_observations(connection, pilot_id)
        saved = _load_pilot(connection, pilot_id)
    if saved is None:
        raise ExpertPilotStoreError("pilot_disappeared")
    return _public_pilot(saved, observations), True


def _load_pilot(connection, pilot_id: UUID):
    return connection.execute(
        """
        SELECT id, candidate_id, evaluation_id, plan_json, plan_sha256,
               audit_json, audit_sha256, attestation_json, attestation_sha256,
               state, created_at, updated_at
        FROM expert_pilots WHERE id = ?
        """,
        (str(pilot_id),),
    ).fetchone()


def _load_observations(connection, pilot_id: UUID) -> tuple[ExpertPilotObservation, ...]:
    rows = connection.execute(
        """
        SELECT observation_json FROM expert_pilot_observations
        WHERE pilot_id = ? ORDER BY created_at, id
        """,
        (str(pilot_id),),
    ).fetchall()
    return tuple(
        ExpertPilotObservation.model_validate_json(row["observation_json"]) for row in rows
    )


def _public_pilot(row, observations: tuple[ExpertPilotObservation, ...] = ()) -> dict[str, object]:
    plan = ExpertPilotPlan.model_validate_json(row["plan_json"])
    metrics = build_pilot_metrics(
        UUID(str(row["id"])), observations, protocol_sha256=plan.measurement_protocol_sha256
    )
    return {
        "id": UUID(str(row["id"])),
        "candidate_id": UUID(str(row["candidate_id"])),
        "evaluation_id": UUID(str(row["evaluation_id"])),
        "plan": plan.model_dump(mode="json"),
        "plan_sha256": row["plan_sha256"],
        "audit": ExpertPilotAudit.model_validate_json(row["audit_json"]).model_dump(mode="json")
        if row["audit_json"] is not None
        else None,
        "audit_sha256": row["audit_sha256"],
        "attestation": (
            ExpertPilotAttestation.model_validate_json(row["attestation_json"]).model_dump(
                mode="json"
            )
            if row["attestation_json"] is not None
            else None
        ),
        "attestation_sha256": row["attestation_sha256"],
        "observations": [item.model_dump(mode="json") for item in observations],
        "metrics": metrics.model_dump(mode="json"),
        "metrics_sha256": metrics.metrics_sha256,
        "state": row["state"],
        "created_at": datetime.fromisoformat(row["created_at"]),
        "updated_at": datetime.fromisoformat(row["updated_at"]),
    }
