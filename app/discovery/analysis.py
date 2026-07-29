"""Deterministic analyses, human code gate, trajectories, and benchmark scoring."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from datetime import datetime
from statistics import fmean, pstdev
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.discovery.contracts import (
    HypothesisCard,
    HypothesisDraft,
    build_hypothesis_card,
    content_hash,
)
from app.discovery.data import (
    ExperimentalDataset,
    FermentationPoint,
    PolyphenolMeasurement,
    SensoryObservation,
    VolatileMeasurement,
)


class DeterministicAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow: Literal["fermentation", "volatiles", "polyphenols", "sensory"]
    workflow_version: Literal["1.0.0"] = "1.0.0"
    metrics: dict[str, float]
    warnings: list[str]
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _result(
    workflow: Literal["fermentation", "volatiles", "polyphenols", "sensory"],
    metrics: dict[str, float],
    warnings: list[str],
) -> DeterministicAnalysisResult:
    payload = {
        "workflow": workflow,
        "workflow_version": "1.0.0",
        "metrics": metrics,
        "warnings": warnings,
    }
    return DeterministicAnalysisResult(
        **payload,
        result_sha256=content_hash(payload),
    )


def analyze_fermentation(dataset: ExperimentalDataset) -> DeterministicAnalysisResult:
    if dataset.kind != "fermentation":
        raise ValueError("fermentation workflow requires fermentation data")
    groups: dict[tuple[str, int], list[FermentationPoint]] = defaultdict(list)
    for record in dataset.records:
        assert isinstance(record, FermentationPoint)
        groups[(record.sample_id, record.replicate)].append(record)
    rates: list[float] = []
    endpoint_changes: list[float] = []
    for points in groups.values():
        ordered = sorted(points, key=lambda item: item.time_hours)
        if len(ordered) < 2:
            continue
        duration = ordered[-1].time_hours - ordered[0].time_hours
        if duration <= 0:
            continue
        change = ordered[-1].density_g_ml - ordered[0].density_g_ml
        endpoint_changes.append(change)
        rates.append(change / duration)
    if not rates:
        raise ValueError("fermentation workflow requires two distinct times per replicate")
    return _result(
        "fermentation",
        {
            "mean_density_change_g_ml": fmean(endpoint_changes),
            "mean_density_rate_g_ml_per_hour": fmean(rates),
            "replicate_count": float(len(rates)),
        },
        [],
    )


def analyze_volatiles(dataset: ExperimentalDataset) -> DeterministicAnalysisResult:
    if dataset.kind != "volatiles":
        raise ValueError("volatile workflow requires volatile data")
    values: dict[str, list[float]] = defaultdict(list)
    for record in dataset.records:
        assert isinstance(record, VolatileMeasurement)
        concentration = (
            record.concentration / 1000 if record.unit == "ug/L" else record.concentration
        )
        values[record.compound].append(concentration)
    return _result(
        "volatiles",
        {f"mean_mg_l:{name}": fmean(items) for name, items in sorted(values.items())},
        [],
    )


def analyze_polyphenols(dataset: ExperimentalDataset) -> DeterministicAnalysisResult:
    if dataset.kind != "polyphenols":
        raise ValueError("polyphenol workflow requires polyphenol data")
    values: dict[str, list[float]] = defaultdict(list)
    for record in dataset.records:
        assert isinstance(record, PolyphenolMeasurement)
        values[record.analyte].append(record.concentration_mg_l)
    return _result(
        "polyphenols",
        {f"mean_mg_l:{name}": fmean(items) for name, items in sorted(values.items())},
        [],
    )


def analyze_sensory(dataset: ExperimentalDataset) -> DeterministicAnalysisResult:
    if dataset.kind != "sensory":
        raise ValueError("sensory workflow requires sensory data")
    values: dict[str, list[float]] = defaultdict(list)
    for record in dataset.records:
        assert isinstance(record, SensoryObservation)
        normalized = (record.score - record.scale_min) / (record.scale_max - record.scale_min)
        values[record.attribute].append(normalized)
    warnings = (
        ["Un seul évaluateur pseudonymisé."]
        if len({record.assessor_pseudonym for record in dataset.records}) < 2
        else []
    )
    return _result(
        "sensory",
        {f"mean_normalized:{name}": fmean(items) for name, items in sorted(values.items())},
        warnings,
    )


DETERMINISTIC_WORKFLOWS = {
    "fermentation": analyze_fermentation,
    "volatiles": analyze_volatiles,
    "polyphenols": analyze_polyphenols,
    "sensory": analyze_sensory,
}


class AnalysisEnvironmentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    python_version: str = Field(pattern=r"^3\.[0-9]+\.[0-9]+$")
    r_version: str | None = Field(default=None, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    packages: dict[str, str]
    network_enabled: Literal[False] = False
    cpu_limit: int = Field(ge=1, le=8)
    memory_limit_gb: float = Field(gt=0, le=12.5)
    timeout_seconds: int = Field(ge=1, le=3600)

    @model_validator(mode="after")
    def pinned_packages(self) -> AnalysisEnvironmentManifest:
        if any(
            re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value) is None
            for value in self.packages.values()
        ):
            raise ValueError("analysis package versions must be exact")
        return self


class GeneratedCodeReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dependencies: dict[str, str]
    input_files: list[str] = Field(min_length=1)
    output_files: list[str] = Field(min_length=1)
    environment: AnalysisEnvironmentManifest
    decision: Literal["approve", "reject"]
    reviewer_reference: str = Field(min_length=3, max_length=200)
    comment: str | None = Field(default=None, max_length=2000)
    reviewed_at: datetime


def require_generated_code_approval(review: GeneratedCodeReview, code: str) -> None:
    if hashlib.sha256(code.encode("utf-8")).hexdigest() != review.code_sha256:
        raise ValueError("reviewed code hash differs from executable code")
    if review.decision != "approve":
        raise PermissionError("generated code requires explicit human approval")
    if review.dependencies != review.environment.packages:
        raise ValueError("reviewed dependencies differ from the isolated environment")


class AnalysisIsolationUnavailableError(RuntimeError):
    """No attested operating-system isolation backend is configured."""


class IsolatedExecutionResult(BaseModel):
    """Content-addressed receipt returned by an external isolation backend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    backend: str = Field(min_length=1, max_length=100)
    isolation_reference: str = Field(min_length=3, max_length=500)
    code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_sha256: dict[str, str] = Field(min_length=1)
    duration_seconds: float = Field(ge=0)
    peak_memory_gb: float = Field(ge=0)
    exit_code: Literal[0] = 0

    @model_validator(mode="after")
    def valid_output_hashes(self) -> IsolatedExecutionResult:
        if any(
            re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in self.output_sha256.values()
        ):
            raise ValueError("isolated output hashes must be SHA-256 values")
        return self


class AnalysisExecutor(Protocol):
    """Adapter boundary for a future Windows sandbox or container backend."""

    def execute(
        self,
        *,
        code: str,
        review: GeneratedCodeReview,
    ) -> IsolatedExecutionResult: ...


def execute_reviewed_analysis(
    *,
    code: str,
    review: GeneratedCodeReview,
    executor: AnalysisExecutor | None = None,
) -> IsolatedExecutionResult:
    """Run only approved code through an explicitly supplied isolation backend."""

    require_generated_code_approval(review, code)
    if executor is None:
        raise AnalysisIsolationUnavailableError(
            "no attested analysis isolation backend is configured"
        )
    receipt = executor.execute(code=code, review=review)
    expected_environment = content_hash(review.environment.model_dump(mode="json"))
    if receipt.code_sha256 != review.code_sha256:
        raise ValueError("isolation receipt refers to another code revision")
    if receipt.environment_sha256 != expected_environment:
        raise ValueError("isolation receipt refers to another environment")
    if set(receipt.output_sha256) != set(review.output_files):
        raise ValueError("isolation receipt outputs differ from the reviewed outputs")
    return receipt


class AnalysisRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: UUID
    dataset_id: UUID
    notebook_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameters_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: DeterministicAnalysisResult
    approved_by: str = Field(min_length=3, max_length=200)
    created_at: datetime


def build_analysis_record(
    *,
    dataset_id: UUID,
    notebook: bytes,
    code: str,
    parameters: dict[str, object],
    input_sha256: str,
    result: DeterministicAnalysisResult,
    review: GeneratedCodeReview,
    created_at: datetime,
) -> AnalysisRecord:
    require_generated_code_approval(review, code)
    return AnalysisRecord(
        id=uuid4(),
        dataset_id=dataset_id,
        notebook_sha256=hashlib.sha256(notebook).hexdigest(),
        code_sha256=review.code_sha256,
        parameters_sha256=content_hash(parameters),
        input_sha256=input_sha256,
        output_sha256=content_hash(result.model_dump(mode="json")),
        result=result,
        approved_by=review.reviewer_reference,
        created_at=created_at,
    )


class AnalysisTrajectory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=1, le=4)
    parameters: dict[str, str | int | float | bool]
    result: dict[str, float] | None = None
    error: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def exactly_one_outcome(self) -> AnalysisTrajectory:
        if (self.result is None) == (self.error is None):
            raise ValueError("trajectory requires exactly one result or error")
        return self


def trajectory_limit(profile: Literal["8gb", "16gb"], remaining_quota: int) -> int:
    return min(2 if profile == "8gb" else 4, max(0, remaining_quota))


class TrajectoryConsensus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_means: dict[str, float]
    metric_minima: dict[str, float]
    metric_maxima: dict[str, float]
    disagreements: dict[str, float]
    failed_trajectories: list[int]
    parameter_choices: list[dict[str, str | int | float | bool]]


def build_trajectory_consensus(trajectories: list[AnalysisTrajectory]) -> TrajectoryConsensus:
    successful = [item for item in trajectories if item.result is not None]
    if not successful:
        raise ValueError("consensus requires at least one successful trajectory")
    metric_names = set.intersection(*(set(item.result or {}) for item in successful))
    if not metric_names:
        raise ValueError("successful trajectories share no metric")
    values = {
        name: [float(item.result[name]) for item in successful if item.result is not None]
        for name in sorted(metric_names)
    }
    return TrajectoryConsensus(
        metric_means={name: fmean(items) for name, items in values.items()},
        metric_minima={name: min(items) for name, items in values.items()},
        metric_maxima={name: max(items) for name, items in values.items()},
        disagreements={
            name: pstdev(items) if len(items) > 1 else 0.0 for name, items in values.items()
        },
        failed_trajectories=[item.index for item in trajectories if item.error is not None],
        parameter_choices=[item.parameters for item in trajectories],
    )


class DiscoveryCycleApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    previous_hypothesis_id: UUID
    analysis_id: UUID
    decision: Literal["approve_next", "stop"]
    expert_reference: str = Field(min_length=3, max_length=200)
    literature_evidence_ids: list[str] = Field(min_length=1)
    experimental_dataset_ids: list[UUID] = Field(min_length=1)
    comment: str | None = Field(default=None, max_length=2000)


def build_next_cycle_hypothesis(
    *,
    approval: DiscoveryCycleApproval,
    question: str,
    draft: HypothesisDraft,
    validated_literature_evidence_ids: set[str],
    corpus_sha256: str,
    model_sha256: str,
    prompt_sha256: str,
    created_at: datetime,
) -> HypothesisCard:
    if approval.decision != "approve_next":
        raise PermissionError("the expert stopped this discovery cycle")
    cited = {evidence_id for premise in draft.premises for evidence_id in premise.evidence_ids}
    if not cited.intersection(approval.literature_evidence_ids):
        raise ValueError("next hypothesis must retain literature provenance")
    if not approval.experimental_dataset_ids:
        raise ValueError("next hypothesis must retain experimental provenance")
    return build_hypothesis_card(
        question=question,
        draft=draft,
        validated_evidence_ids=validated_literature_evidence_ids,
        corpus_sha256=corpus_sha256,
        model_sha256=model_sha256,
        prompt_sha256=prompt_sha256,
        parent_hypothesis_id=approval.previous_hypothesis_id,
        source_analysis_ids=[approval.analysis_id],
        experimental_dataset_ids=approval.experimental_dataset_ids,
        created_at=created_at,
    )


class GroundTruthMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected: float
    absolute_tolerance: float = Field(ge=0)


class GroundTruthCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    workflow: Literal["fermentation", "volatiles", "polyphenols", "sensory"]
    metrics: dict[str, GroundTruthMetric] = Field(min_length=1)
    expected_error: str | None = None


class BenchmarkCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    numerically_correct: bool
    reproducible: bool
    failed: bool
    cost_eur: float = Field(ge=0)
    failure_reason: str | None = None


class AssistedAnalysisBenchmarkReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cases: list[BenchmarkCaseResult] = Field(min_length=1)
    exact_accuracy: float = Field(ge=0, le=1)
    reproducibility_rate: float = Field(ge=0, le=1)
    total_cost_eur: float = Field(ge=0)
    failure_rate: float = Field(ge=0, le=1)
    all_cases_passed: bool


def score_benchmark(
    truth: list[GroundTruthCase],
    observed: dict[str, tuple[DeterministicAnalysisResult | None, bool, float, str | None]],
) -> AssistedAnalysisBenchmarkReport:
    results: list[BenchmarkCaseResult] = []
    for case in truth:
        result, reproducible, cost, error = observed.get(case.id, (None, False, 0.0, "missing"))
        correct = result is not None and all(
            name in result.metrics
            and math.isclose(
                result.metrics[name],
                expected.expected,
                abs_tol=expected.absolute_tolerance,
            )
            for name, expected in case.metrics.items()
        )
        failed = error is not None or not correct
        results.append(
            BenchmarkCaseResult(
                case_id=case.id,
                numerically_correct=correct,
                reproducible=reproducible,
                failed=failed,
                cost_eur=cost,
                failure_reason=error if failed else None,
            )
        )
    count = len(results)
    return AssistedAnalysisBenchmarkReport(
        cases=results,
        exact_accuracy=sum(item.numerically_correct for item in results) / count,
        reproducibility_rate=sum(item.reproducible for item in results) / count,
        total_cost_eur=sum(item.cost_eur for item in results),
        failure_rate=sum(item.failed for item in results) / count,
        all_cases_passed=all(not item.failed for item in results),
    )
