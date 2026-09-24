"""Bounded shadow-pilot readiness contracts for the expert-memory roadmap."""

from __future__ import annotations

from math import ceil
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.evaluation.expert_memory import ExpertEvaluationManifest, ExpertEvaluationReport
from app.knowledge.contracts import ImmutableModel, Sha256, ShortText, content_hash


class ExpertPilotError(ValueError):
    """The pilot plan or its readiness evidence is not safe to use."""


PILOT_MEASUREMENT_PROTOCOL_SHA256 = content_hash(
    {
        "schema_version": 1,
        "time_unit": "seconds",
        "percentiles": {"p50": "nearest_rank", "p95": "nearest_rank"},
        "token_total": "prompt_plus_completion",
        "target_observations": {"minimum": 10, "maximum": 20},
    }
)


class ExpertPilotPlan(ImmutableModel):
    """Precommitted validation-only execution; it can never authorize activation."""

    schema_version: Literal[1] = 1
    pilot_id: UUID
    candidate_id: UUID
    evaluation_id: UUID
    manifest_sha256: Sha256
    candidate_sha256: Sha256
    question_hashes: tuple[Sha256, ...]
    control_question_hashes: tuple[Sha256, ...]
    max_llm_requests: int
    measurement_protocol_sha256: Sha256 = PILOT_MEASUREMENT_PROTOCOL_SHA256
    mode: Literal["shadow"] = "shadow"
    activation_allowed: Literal[False] = False
    human_approval_required: Literal[True] = True

    @property
    def plan_sha256(self) -> str:
        return content_hash(self.model_dump(mode="json"))

    def complete_controls(self) -> ExpertPilotPlan:
        if len(self.question_hashes) < 1:
            raise ExpertPilotError("pilot requires at least one question")
        if len(self.control_question_hashes) < 2:
            raise ExpertPilotError("pilot requires two independent controls")
        if len(self.question_hashes) > 100 or len(self.control_question_hashes) > 20:
            raise ExpertPilotError("pilot exceeds its bounded case limit")
        if len(set(self.question_hashes)) != len(self.question_hashes):
            raise ExpertPilotError("pilot question hashes must be unique")
        if len(set(self.control_question_hashes)) != len(self.control_question_hashes):
            raise ExpertPilotError("pilot control hashes must be unique")
        if self.max_llm_requests < 0 or self.max_llm_requests > 10_000:
            raise ExpertPilotError("pilot LLM budget is outside its bound")
        if self.mode != "shadow" or self.activation_allowed or not self.human_approval_required:
            raise ExpertPilotError("pilot must remain shadow-only and human-gated")
        return self


class ExpertPilotAudit(ImmutableModel):
    """Hashable readiness result; ``ready`` still means ready for human sign-off only."""

    schema_version: Literal[1] = 1
    pilot_id: UUID
    plan_sha256: Sha256
    manifest_sha256: Sha256
    candidate_id: UUID
    evaluation_id: UUID
    candidate_state: ShortText
    evaluation_state: ShortText
    report_sha256: Sha256 | None
    state: Literal["ready", "blocked"]
    blockers: tuple[ShortText, ...] = ()
    activation_allowed: Literal[False] = False
    human_approval_required: Literal[True] = True

    @property
    def audit_sha256(self) -> str:
        return content_hash(self.model_dump(mode="json"))


class ExpertPilotAttestation(ImmutableModel):
    """An explicit external decision about a pilot audit, never an activation credential."""

    schema_version: Literal[1] = 1
    attestation_id: UUID
    pilot_id: UUID
    audit_sha256: Sha256
    reviewer_label: ShortText
    external_reference: ShortText
    decision: Literal["accept", "reject"]
    reason: ShortText
    activation_allowed: Literal[False] = False

    @property
    def attestation_sha256(self) -> str:
        return content_hash(self.model_dump(mode="json"))


class ExpertPilotObservation(ImmutableModel):
    """One human-recorded pilot case; it contains no conversation or scientific prose."""

    schema_version: Literal[1] = 1
    observation_id: UUID
    pilot_id: UUID
    case_sha256: Sha256
    protocol_sha256: Sha256
    expert_time_seconds: int = Field(strict=True, ge=1, le=86_400)
    diagnosis_human_corrected: bool
    useful_effect: Literal["useful", "neutral", "harmful", "unknown"]
    false_gain: bool
    rollback_count: int = Field(strict=True, ge=0, le=10)
    prompt_tokens: int = Field(strict=True, ge=0, le=100_000)
    completion_tokens: int = Field(strict=True, ge=0, le=100_000)
    recorded_by: ShortText

    @property
    def observation_sha256(self) -> str:
        return content_hash(self.model_dump(mode="json"))


class ExpertPilotMetrics(ImmutableModel):
    """Deterministic aggregate used to decide whether a real pilot is complete."""

    schema_version: Literal[1] = 1
    pilot_id: UUID
    protocol_sha256: Sha256
    observation_count: int = Field(strict=True, ge=0, le=20)
    minimum_observations: Literal[10] = 10
    maximum_observations: Literal[20] = 20
    complete: bool
    p50_expert_time_seconds: int | None = Field(default=None, ge=1)
    p95_expert_time_seconds: int | None = Field(default=None, ge=1)
    p50_total_tokens: int | None = Field(default=None, ge=0)
    p95_total_tokens: int | None = Field(default=None, ge=0)
    diagnosis_human_corrected_count: int = Field(strict=True, ge=0, le=20)
    useful_effect_count: int = Field(strict=True, ge=0, le=20)
    false_gain_count: int = Field(strict=True, ge=0, le=20)
    rollback_count: int = Field(strict=True, ge=0, le=200)

    @property
    def metrics_sha256(self) -> str:
        return content_hash(self.model_dump(mode="json"))


def attest_expert_pilot(
    audit: ExpertPilotAudit,
    *,
    attestation_id: UUID,
    reviewer_label: str,
    external_reference: str,
    decision: Literal["accept", "reject"],
    reason: str,
) -> ExpertPilotAttestation:
    """Record only caller-supplied external authority for one exact pilot audit."""

    if audit.state != "ready" and decision == "accept":
        raise ExpertPilotError("blocked pilot cannot be externally accepted")
    if not reviewer_label.strip() or not external_reference.strip() or not reason.strip():
        raise ExpertPilotError("pilot attestation requires reviewer, reference, and reason")
    return ExpertPilotAttestation(
        attestation_id=attestation_id,
        pilot_id=audit.pilot_id,
        audit_sha256=audit.audit_sha256,
        reviewer_label=reviewer_label,
        external_reference=external_reference,
        decision=decision,
        reason=reason,
    )


def build_expert_pilot_plan(
    manifest: ExpertEvaluationManifest,
    *,
    pilot_id: UUID,
    evaluation_id: UUID,
    candidate_sha256: str,
    measurement_protocol_sha256: str = PILOT_MEASUREMENT_PROTOCOL_SHA256,
) -> ExpertPilotPlan:
    """Create a validation-only pilot plan from a frozen C8 manifest."""

    manifest.complete_controls()
    if manifest.split != "validation":
        raise ExpertPilotError("pilot requires the validation split")
    plan = ExpertPilotPlan(
        pilot_id=pilot_id,
        candidate_id=manifest.candidate_id,
        evaluation_id=evaluation_id,
        manifest_sha256=manifest.manifest_sha256,
        candidate_sha256=candidate_sha256,
        question_hashes=manifest.question_hashes,
        control_question_hashes=manifest.control_question_hashes,
        max_llm_requests=manifest.max_llm_requests,
        measurement_protocol_sha256=measurement_protocol_sha256,
    )
    return plan.complete_controls()


def audit_expert_pilot(
    plan: ExpertPilotPlan,
    *,
    candidate_state: str,
    evaluation_id: UUID,
    evaluation_report: ExpertEvaluationReport | None,
) -> ExpertPilotAudit:
    """Audit readiness without turning deterministic execution into scientific approval."""

    plan.complete_controls()
    blockers: list[str] = []
    if evaluation_id != plan.evaluation_id:
        blockers.append("evaluation_identity_mismatch")
    report_sha256 = evaluation_report.report_sha256 if evaluation_report is not None else None
    evaluation_state = evaluation_report.state if evaluation_report is not None else "missing"
    if evaluation_report is None:
        blockers.append("evaluation_report_missing")
    else:
        if evaluation_report.manifest_sha256 != plan.manifest_sha256:
            blockers.append("evaluation_manifest_hash_mismatch")
        if evaluation_report.state != "passed":
            blockers.append("evaluation_not_passed")
        if not evaluation_report.deterministic_validators_passed:
            blockers.append("deterministic_validators_failed")
        if not _metric_at_least_one(evaluation_report, "execution_success_rate"):
            blockers.append("execution_success_rate_below_one")
        if not _metric_at_least_one(evaluation_report, "control_execution_success_rate"):
            blockers.append("control_execution_success_rate_below_one")
    if candidate_state != "awaiting_review":
        blockers.append("candidate_not_awaiting_review")
    return ExpertPilotAudit(
        pilot_id=plan.pilot_id,
        plan_sha256=plan.plan_sha256,
        manifest_sha256=plan.manifest_sha256,
        candidate_id=plan.candidate_id,
        evaluation_id=plan.evaluation_id,
        candidate_state=candidate_state,
        evaluation_state=evaluation_state,
        report_sha256=report_sha256,
        state="ready" if not blockers else "blocked",
        blockers=tuple(blockers),
    )


def _metric_at_least_one(report: ExpertEvaluationReport, name: str) -> bool:
    metric = next((item for item in report.metrics if item.name == name), None)
    return metric is not None and metric.base >= 1.0 and metric.candidate >= 1.0


def build_pilot_metrics(
    pilot_id: UUID,
    observations: tuple[ExpertPilotObservation, ...],
    *,
    protocol_sha256: str,
) -> ExpertPilotMetrics:
    """Aggregate bounded observations with a predeclared nearest-rank protocol."""

    if len(observations) > 20:
        raise ExpertPilotError("pilot observation limit exceeded")
    if any(item.pilot_id != pilot_id for item in observations):
        raise ExpertPilotError("pilot observation identity mismatch")
    if any(item.protocol_sha256 != protocol_sha256 for item in observations):
        raise ExpertPilotError("pilot measurement protocol mismatch")
    if len({item.case_sha256 for item in observations}) != len(observations):
        raise ExpertPilotError("pilot observation cases must be unique")
    times = sorted(item.expert_time_seconds for item in observations)
    token_totals = sorted(item.prompt_tokens + item.completion_tokens for item in observations)
    return ExpertPilotMetrics(
        pilot_id=pilot_id,
        protocol_sha256=protocol_sha256,
        observation_count=len(observations),
        complete=len(observations) >= 10,
        p50_expert_time_seconds=_nearest_rank(times, 0.50),
        p95_expert_time_seconds=_nearest_rank(times, 0.95),
        p50_total_tokens=_nearest_rank(token_totals, 0.50),
        p95_total_tokens=_nearest_rank(token_totals, 0.95),
        diagnosis_human_corrected_count=sum(
            item.diagnosis_human_corrected for item in observations
        ),
        useful_effect_count=sum(item.useful_effect == "useful" for item in observations),
        false_gain_count=sum(item.false_gain for item in observations),
        rollback_count=sum(item.rollback_count for item in observations),
    )


def _nearest_rank(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    return values[max(0, ceil(percentile * len(values)) - 1)]
