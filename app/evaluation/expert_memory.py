"""Bounded, label-free planning contracts for expert-memory evaluations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from app.database.sqlite import Database
from app.evaluation.campaign import EvaluationCampaignSpec, EvaluationCellSpec
from app.jobs.contracts import ExpertImprovementPayload, ExpertMemoryPin
from app.jobs.repository import JobRecord, JobRepository
from app.knowledge.contracts import ImmutableModel, PositiveInt, Sha256, ShortText, content_hash


class ExpertEvaluationError(ValueError):
    """Stable validation error for private expert-evaluation plans."""


class ExpertEvaluationEvidenceError(ValueError):
    """A campaign output cannot be used as deterministic evaluation evidence."""


class ExpertEvaluationManifest(ImmutableModel):
    """Frozen inputs for a development/validation evaluation, without labels or prose."""

    schema_version: Literal[1] = 1
    candidate_id: UUID
    base_release_id: UUID
    candidate_release_id: UUID
    corpus_snapshot_sha256: Sha256
    configuration_sha256: Sha256
    code_revision: ShortText
    split: Literal["development", "validation"]
    question_hashes: tuple[Sha256, ...]
    control_question_hashes: tuple[Sha256, ...]
    mode: Literal["abstract_only", "full_text"]
    max_llm_requests: int
    argo_authorized: bool

    @property
    def manifest_sha256(self) -> str:
        return content_hash(self.model_dump(mode="json"))

    def complete_controls(self) -> ExpertEvaluationManifest:
        if len(self.question_hashes) < 1:
            raise ValueError("evaluation manifest requires at least one question")
        if len(self.control_question_hashes) < 2:
            raise ValueError("evaluation manifest requires two independent controls")
        if len(self.question_hashes) > 100 or len(self.control_question_hashes) > 20:
            raise ValueError("evaluation manifest exceeds its bounded case limit")
        if len(set(self.question_hashes)) != len(self.question_hashes):
            raise ValueError("evaluation question hashes must be unique")
        if len(set(self.control_question_hashes)) != len(self.control_question_hashes):
            raise ValueError("evaluation control hashes must be unique")
        if self.max_llm_requests < 0 or self.max_llm_requests > 10_000:
            raise ValueError("evaluation LLM budget is outside its bound")
        return self


class ExpertEvaluationCase(ImmutableModel):
    """One label-free prompt exposed to both isolated campaign arms."""

    question_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    profile: Literal["p0", "p1", "p2"]
    message: str = Field(min_length=2, max_length=4000)

    @property
    def question_sha256(self) -> str:
        return sha256(" ".join(self.message.split()).encode("utf-8")).hexdigest()


def build_campaign_pair_specs(
    database: Database,
    manifest: ExpertEvaluationManifest,
    cases: tuple[ExpertEvaluationCase, ...],
    *,
    run_id_prefix: str,
) -> tuple[EvaluationCampaignSpec, EvaluationCampaignSpec]:
    """Build reproducible base/candidate specs from a hash-only manifest and prompt cases."""

    manifest.complete_controls()
    if not cases:
        raise ExpertEvaluationError("evaluation_cases_empty")
    if len({case.question_id for case in cases}) != len(cases):
        raise ExpertEvaluationError("evaluation_cases_not_unique")
    expected_hashes = set(manifest.question_hashes) | set(manifest.control_question_hashes)
    observed_hashes = {case.question_sha256 for case in cases}
    if observed_hashes != expected_hashes:
        raise ExpertEvaluationError("evaluation_case_hashes_mismatch")
    if len(cases) > 120:
        raise ExpertEvaluationError("evaluation_cases_too_large")

    def release_pin(release_id: UUID) -> ExpertMemoryPin:
        with database.connect() as connection:
            release = connection.execute(
                "SELECT package_sha256, state FROM expert_releases WHERE id = ?",
                (str(release_id),),
            ).fetchone()
        if release is None or release["state"] not in {"candidate", "eligible"}:
            raise ExpertEvaluationError("evaluation_release_not_available")
        return ExpertMemoryPin(
            mode="shadow",
            release_id=release_id,
            release_sha256=release["package_sha256"],
        )

    cells = [
        EvaluationCellSpec(
            question_id=case.question_id,
            profile=case.profile,
            message=case.message,
        )
        for case in cases
    ]
    return (
        EvaluationCampaignSpec(
            run_id=f"{run_id_prefix}-base",
            cells=cells,
            expert_memory_pin=release_pin(manifest.base_release_id),
        ),
        EvaluationCampaignSpec(
            run_id=f"{run_id_prefix}-candidate",
            cells=cells,
            expert_memory_pin=release_pin(manifest.candidate_release_id),
        ),
    )


def enqueue_evaluation_comparison_job(
    database: Database,
    *,
    evaluation_id: UUID,
    base_state_path: str | Path,
    candidate_state_path: str | Path,
    conversation_id: UUID,
    user_message_id: UUID,
    client_request_id: UUID,
) -> JobRecord:
    """Enqueue one idempotent comparison job after both campaign states are complete."""

    if get_evaluation(database, evaluation_id) is None:
        raise ExpertEvaluationError("evaluation_not_found")
    _load_campaign_state(base_state_path, label="base")
    _load_campaign_state(candidate_state_path, label="candidate")
    payload = ExpertImprovementPayload(
        operation="evaluate",
        evaluation_id=evaluation_id,
        base_state_path=str(base_state_path),
        candidate_state_path=str(candidate_state_path),
        conversation_id=conversation_id,
        client_request_id=client_request_id,
    )
    return JobRepository(database.path).enqueue_expert_improvement(
        payload,
        user_message_id=user_message_id,
    )


def enqueue_evaluation_orchestrator_job(
    database: Database,
    *,
    evaluation_id: UUID,
    campaign_pair_path: str | Path,
    client_request_id: UUID,
) -> JobRecord:
    """Queue one parent that advances the isolated campaigns and comparison job."""

    evaluation = get_evaluation(database, evaluation_id)
    if evaluation is None:
        raise ExpertEvaluationError("evaluation_not_found")
    with database.connect() as connection:
        anchor = connection.execute(
            """
            SELECT m.id AS message_id, m.conversation_id
            FROM expert_candidates AS c
            JOIN expert_diagnoses AS d ON d.id = c.diagnosis_id
            JOIN expert_corrections AS ec ON ec.id = d.correction_id
            JOIN chat_messages AS m ON m.id = ec.message_id
            WHERE c.id = ?
            """,
            (str(evaluation["candidate_id"]),),
        ).fetchone()
    if anchor is None:
        raise ExpertEvaluationError("evaluation_anchor_not_found")
    payload = ExpertImprovementPayload(
        operation="orchestrate",
        evaluation_id=evaluation_id,
        campaign_pair_path=str(campaign_pair_path),
        conversation_id=UUID(str(anchor["conversation_id"])),
        client_request_id=client_request_id,
    )
    return JobRepository(database.path).enqueue_expert_improvement(
        payload,
        user_message_id=UUID(str(anchor["message_id"])),
    )


class EvaluationMetric(ImmutableModel):
    name: ShortText
    base: float
    candidate: float
    delta: float


class ExpertEvaluationReport(ImmutableModel):
    """A bounded comparison report; it carries no labels or correction prose."""

    schema_version: Literal[1] = 1
    evaluation_id: UUID
    manifest_sha256: Sha256
    base_report_sha256: Sha256
    candidate_report_sha256: Sha256
    state: Literal["passed", "failed", "inconclusive"]
    case_count: PositiveInt
    control_case_count: PositiveInt
    deterministic_validators_passed: bool
    metrics: tuple[EvaluationMetric, ...] = ()

    @property
    def report_sha256(self) -> str:
        return content_hash(self.model_dump(mode="json"))

    def complete_controls(self) -> ExpertEvaluationReport:
        if self.control_case_count < 2 or self.case_count < self.control_case_count:
            raise ValueError("evaluation report has insufficient independent controls")
        if self.state == "passed" and not self.deterministic_validators_passed:
            raise ValueError("a passed evaluation requires deterministic validators")
        if len(self.metrics) > 20:
            raise ValueError("evaluation report exceeds its metric bound")
        return self


def build_campaign_comparison_report(
    manifest: ExpertEvaluationManifest,
    *,
    evaluation_id: UUID,
    base_state_path: str | Path,
    candidate_state_path: str | Path,
) -> ExpertEvaluationReport:
    """Build a label-free report from two completed, isolated campaign states.

    This measures execution integrity only. A complete pair is therefore
    ``inconclusive`` until a separately authorized scientific adjudicator supplies
    comparison criteria; it must never be promoted from this report alone.
    """

    manifest.complete_controls()
    base = _load_campaign_state(base_state_path, label="base")
    candidate = _load_campaign_state(candidate_state_path, label="candidate")
    base_cells = _normalize_campaign_cells(base, label="base")
    candidate_cells = _normalize_campaign_cells(candidate, label="candidate")
    expected_hashes = set(manifest.question_hashes) | set(manifest.control_question_hashes)
    for label, cells in (("base", base_cells), ("candidate", candidate_cells)):
        observed = [str(cell["question_sha256"]) for cell in cells]
        if not expected_hashes.issuperset(observed):
            raise ExpertEvaluationEvidenceError(f"{label} campaign contains an unknown question")
        if set(observed) != expected_hashes:
            raise ExpertEvaluationEvidenceError(f"{label} campaign question coverage is incomplete")
    if [cell["key"] for cell in base_cells] != [cell["key"] for cell in candidate_cells]:
        raise ExpertEvaluationEvidenceError("base and candidate campaign cells do not match")
    if [cell["question_sha256"] for cell in base_cells] != [
        cell["question_sha256"] for cell in candidate_cells
    ]:
        raise ExpertEvaluationEvidenceError("base and candidate question identities do not match")

    base_hash = content_hash({"run_id": base["run_id"], "cells": base_cells})
    candidate_hash = content_hash({"run_id": candidate["run_id"], "cells": candidate_cells})
    control_hashes = set(manifest.control_question_hashes)
    base_successes = sum(cell["state"] == "succeeded" for cell in base_cells)
    candidate_successes = sum(cell["state"] == "succeeded" for cell in candidate_cells)
    base_controls = [cell for cell in base_cells if cell["question_sha256"] in control_hashes]
    candidate_controls = [
        cell for cell in candidate_cells if cell["question_sha256"] in control_hashes
    ]
    all_successful = (
        len(base_cells) == len(candidate_cells) == len(expected_hashes)
        and base_successes == len(base_cells)
        and candidate_successes == len(candidate_cells)
        and len(base_controls) == len(candidate_controls) == len(control_hashes)
    )
    base_control_successes = sum(cell["state"] == "succeeded" for cell in base_controls)
    candidate_control_successes = sum(cell["state"] == "succeeded" for cell in candidate_controls)
    metrics = (
        EvaluationMetric(
            name="execution_success_rate",
            base=base_successes / len(base_cells),
            candidate=candidate_successes / len(candidate_cells),
            delta=(candidate_successes - base_successes) / len(base_cells),
        ),
        EvaluationMetric(
            name="control_execution_success_rate",
            base=base_control_successes / len(base_controls),
            candidate=candidate_control_successes / len(candidate_controls),
            delta=(candidate_control_successes - base_control_successes) / len(base_controls),
        ),
    )
    return ExpertEvaluationReport(
        evaluation_id=evaluation_id,
        manifest_sha256=manifest.manifest_sha256,
        base_report_sha256=base_hash,
        candidate_report_sha256=candidate_hash,
        state="inconclusive" if all_successful else "failed",
        case_count=len(expected_hashes),
        control_case_count=len(control_hashes),
        deterministic_validators_passed=all_successful,
        metrics=metrics,
    )


def evaluate_campaign_pair(
    database: Database,
    evaluation_id: UUID,
    *,
    base_state_path: str | Path,
    candidate_state_path: str | Path,
    now: datetime | None = None,
) -> tuple[dict[str, object], bool]:
    """Build and persist one report from two completed campaign state files."""

    evaluation = get_evaluation(database, evaluation_id)
    if evaluation is None:
        raise ExpertEvaluationError("evaluation_not_found")
    manifest = ExpertEvaluationManifest.model_validate(evaluation["manifest"])
    report = build_campaign_comparison_report(
        manifest,
        evaluation_id=evaluation_id,
        base_state_path=base_state_path,
        candidate_state_path=candidate_state_path,
    )
    return persist_evaluation_report(database, report, now=now)


def _load_campaign_state(path: str | Path, *, label: str) -> dict[str, object]:
    candidate = Path(path)
    try:
        payload = candidate.read_bytes()
    except OSError as error:
        raise ExpertEvaluationEvidenceError(f"{label} campaign state is unavailable") from error
    if len(payload) > 1_048_576:
        raise ExpertEvaluationEvidenceError(f"{label} campaign state exceeds its size limit")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExpertEvaluationEvidenceError(f"{label} campaign state is not valid JSON") from error
    if not isinstance(value, dict) or not isinstance(value.get("run_id"), str):
        raise ExpertEvaluationEvidenceError(f"{label} campaign state has no stable run id")
    if value.get("status") != "completed":
        raise ExpertEvaluationEvidenceError(f"{label} campaign is not completed")
    return value


def _normalize_campaign_cells(state: Mapping[str, object], *, label: str) -> list[dict[str, str]]:
    raw_cells = state.get("cells")
    if not isinstance(raw_cells, dict) or not raw_cells:
        raise ExpertEvaluationEvidenceError(f"{label} campaign has no cells")
    normalized: list[dict[str, str]] = []
    for key, raw in raw_cells.items():
        if not isinstance(key, str) or not isinstance(raw, dict):
            raise ExpertEvaluationEvidenceError(f"{label} campaign has an invalid cell")
        question_hash = raw.get("question_sha256")
        cell_state = raw.get("state")
        if (
            not isinstance(question_hash, str)
            or len(question_hash) != 64
            or any(character not in "0123456789abcdef" for character in question_hash)
            or cell_state not in {"succeeded", "failed", "cancelled"}
        ):
            raise ExpertEvaluationEvidenceError(f"{label} campaign has an invalid cell outcome")
        normalized.append({"key": key, "question_sha256": question_hash, "state": str(cell_state)})
    normalized.sort(key=lambda cell: cell["key"])
    return normalized


def plan_evaluation(
    database: Database,
    manifest: ExpertEvaluationManifest,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, object], bool]:
    """Persist one immutable plan after checking candidate identity and state."""

    manifest.complete_controls()
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    manifest_sha256 = manifest.manifest_sha256
    with database.transaction() as connection:
        candidate = connection.execute(
            """
            SELECT id, base_release_id, candidate_release_id, state
            FROM expert_candidates WHERE id = ?
            """,
            (str(manifest.candidate_id),),
        ).fetchone()
        if candidate is None:
            raise ExpertEvaluationError("candidate_not_found")
        if candidate["state"] != "structurally_valid":
            raise ExpertEvaluationError("candidate_not_ready_for_evaluation")
        if candidate["base_release_id"] != str(manifest.base_release_id) or candidate[
            "candidate_release_id"
        ] != str(manifest.candidate_release_id):
            raise ExpertEvaluationError("candidate_release_identity_mismatch")
        existing = connection.execute(
            """
            SELECT id, candidate_id, manifest_json, manifest_sha256,
                   report_json, report_sha256, state, created_at, updated_at
            FROM expert_evaluations
            WHERE candidate_id = ? AND manifest_sha256 = ?
            """,
            (str(manifest.candidate_id), manifest_sha256),
        ).fetchone()
        if existing is not None:
            return _public_evaluation(existing), False
        evaluation_id = uuid4()
        iso_timestamp = timestamp.isoformat()
        connection.execute(
            """
            INSERT INTO expert_evaluations(
                id, candidate_id, manifest_json, manifest_sha256,
                report_json, report_sha256, state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, NULL, NULL, 'planned', ?, ?)
            """,
            (
                str(evaluation_id),
                str(manifest.candidate_id),
                manifest.model_dump_json(),
                manifest_sha256,
                iso_timestamp,
                iso_timestamp,
            ),
        )
        row = connection.execute(
            """
            SELECT id, candidate_id, manifest_json, manifest_sha256,
                   report_json, report_sha256, state, created_at, updated_at
            FROM expert_evaluations WHERE id = ?
            """,
            (str(evaluation_id),),
        ).fetchone()
    if row is None:
        raise ExpertEvaluationError("evaluation_disappeared")
    return _public_evaluation(row), True


def get_evaluation(database: Database, evaluation_id: UUID) -> dict[str, object] | None:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT id, candidate_id, manifest_json, manifest_sha256,
                   report_json, report_sha256, state, created_at, updated_at
            FROM expert_evaluations WHERE id = ?
            """,
            (str(evaluation_id),),
        ).fetchone()
    return _public_evaluation(row) if row is not None else None


def persist_evaluation_report(
    database: Database,
    report: ExpertEvaluationReport,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, object], bool]:
    """Persist one report only against its exact planned manifest and candidate."""

    report.complete_controls()
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    report_sha256 = report.report_sha256
    with database.transaction() as connection:
        evaluation = connection.execute(
            """
            SELECT id, candidate_id, manifest_sha256, report_json, report_sha256, state
            FROM expert_evaluations WHERE id = ?
            """,
            (str(report.evaluation_id),),
        ).fetchone()
        if evaluation is None:
            raise ExpertEvaluationError("evaluation_not_found")
        if evaluation["manifest_sha256"] != report.manifest_sha256:
            raise ExpertEvaluationError("evaluation_manifest_hash_mismatch")
        if evaluation["report_sha256"] is not None:
            if evaluation["report_sha256"] != report_sha256:
                raise ExpertEvaluationError("evaluation_report_conflict")
            return _public_evaluation(evaluation), False
        next_candidate_state = {
            "passed": "awaiting_review",
            "failed": "evaluation_failed",
            "inconclusive": "inconclusive",
        }[report.state]
        connection.execute(
            """
            UPDATE expert_evaluations
            SET report_json = ?, report_sha256 = ?, state = ?, updated_at = ?
            WHERE id = ? AND report_sha256 IS NULL
            """,
            (
                report.model_dump_json(),
                report_sha256,
                report.state,
                timestamp,
                str(report.evaluation_id),
            ),
        )
        connection.execute(
            """
            UPDATE expert_candidates
            SET state = ?, updated_at = ?
            WHERE id = ? AND state IN ('evaluating', 'structurally_valid')
            """,
            (next_candidate_state, timestamp, str(evaluation["candidate_id"])),
        )
        row = connection.execute(
            """
            SELECT id, candidate_id, manifest_json, manifest_sha256,
                   report_json, report_sha256, state, created_at, updated_at
            FROM expert_evaluations WHERE id = ?
            """,
            (str(report.evaluation_id),),
        ).fetchone()
    if row is None:
        raise ExpertEvaluationError("evaluation_disappeared")
    return _public_evaluation(row), True


def _public_evaluation(row) -> dict[str, object]:
    report = (
        ExpertEvaluationReport.model_validate_json(row["report_json"]).model_dump(mode="json")
        if row["report_json"] is not None
        else None
    )
    return {
        "id": UUID(str(row["id"])),
        "candidate_id": UUID(str(row["candidate_id"])),
        "manifest": ExpertEvaluationManifest.model_validate_json(row["manifest_json"]).model_dump(
            mode="json"
        ),
        "manifest_sha256": row["manifest_sha256"],
        "report": report,
        "report_sha256": row["report_sha256"],
        "state": row["state"],
        "created_at": datetime.fromisoformat(row["created_at"]),
        "updated_at": datetime.fromisoformat(row["updated_at"]),
    }
