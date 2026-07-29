"""Signed, precommitted CiderQA ablation plans and comparisons."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.evaluation.ciderqa import CiderQASplit
from app.evaluation.ciderqa_promotion import PromotionMetrics, metrics_from_report
from app.evaluation.ciderqa_report import (
    JsonScalar,
    SignedCiderQAReport,
    canonical_json,
    verify_ciderqa_report,
)

AblationVariant = Literal[
    "baseline",
    "query_variants",
    "reranker",
    "contextual_summary",
    "iteration",
    "citation_traversal",
]

ABLATION_VARIANTS: tuple[AblationVariant, ...] = (
    "baseline",
    "query_variants",
    "reranker",
    "contextual_summary",
    "iteration",
    "citation_traversal",
)

PARAMETER_NAMES = {
    "query_variants": "drs_query_variants_enabled",
    "reranker": "drs_reranker_enabled",
    "contextual_summary": "drs_contextual_summary_enabled",
    "iteration": "drs_iteration_enabled",
    "citation_traversal": "drs_citation_traversal_enabled",
}


class DRSStageConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    variant: AblationVariant
    query_variants: bool
    reranker: bool
    contextual_summary: bool
    iteration: bool
    citation_traversal: bool

    @property
    def sha256(self) -> str:
        payload = canonical_json(self.model_dump(mode="json"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def signed_parameters(self) -> dict[str, JsonScalar]:
        return {
            "ablation_variant": self.variant,
            "ablation_config_sha256": self.sha256,
            **{parameter: getattr(self, stage) for stage, parameter in PARAMETER_NAMES.items()},
        }


def fixed_ablation_configurations() -> list[DRSStageConfiguration]:
    configurations: list[DRSStageConfiguration] = []
    for variant in ABLATION_VARIANTS:
        enabled = variant if variant != "baseline" else None
        configurations.append(
            DRSStageConfiguration(
                variant=variant,
                query_variants=enabled == "query_variants",
                reranker=enabled == "reranker",
                contextual_summary=enabled == "contextual_summary",
                iteration=enabled == "iteration",
                citation_traversal=enabled == "citation_traversal",
            )
        )
    return configurations


class CiderQAAblationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    created_at: datetime
    dataset_version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: CiderQASplit
    mode: Literal["abstract_only", "full_text"]
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_revision: str = Field(min_length=7, max_length=64)
    model_versions: dict[str, str] = Field(min_length=1)
    seeds: dict[str, int] = Field(min_length=1)
    configurations: list[DRSStageConfiguration] = Field(min_length=6, max_length=6)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _content_hash(model: BaseModel, signature_field: str) -> str:
    payload = model.model_dump(mode="json", exclude={signature_field})
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_ablation_plan(
    *,
    dataset_version: str,
    dataset_sha256: str,
    split: CiderQASplit,
    mode: Literal["abstract_only", "full_text"],
    corpus_sha256: str,
    code_revision: str,
    model_versions: dict[str, str],
    seeds: dict[str, int],
    created_at: datetime | None = None,
) -> CiderQAAblationPlan:
    plan = CiderQAAblationPlan(
        created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
        dataset_version=dataset_version,
        dataset_sha256=dataset_sha256,
        split=split,
        mode=mode,
        corpus_sha256=corpus_sha256,
        code_revision=code_revision,
        model_versions=model_versions,
        seeds=seeds,
        configurations=fixed_ablation_configurations(),
        plan_sha256="0" * 64,
    )
    return plan.model_copy(update={"plan_sha256": _content_hash(plan, "plan_sha256")})


def verify_ablation_plan(plan: CiderQAAblationPlan) -> bool:
    if _content_hash(plan, "plan_sha256") != plan.plan_sha256:
        return False
    return plan.configurations == fixed_ablation_configurations()


class AblationComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant: AblationVariant
    enabled_stage: str | None
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: PromotionMetrics
    delta_from_baseline: dict[str, float]
    duration_delta_seconds: float
    peak_process_rss_delta_gb: float
    peak_system_used_delta_gb: float
    argo_request_delta: int
    argo_cost_delta_eur: float


class SignedCiderQAAblationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    created_at: datetime
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: CiderQASplit
    mode: Literal["abstract_only", "full_text"]
    baseline_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    comparisons: list[AblationComparison] = Field(min_length=6, max_length=6)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _validate_source_report(
    *,
    plan: CiderQAAblationPlan,
    configuration: DRSStageConfiguration,
    report: SignedCiderQAReport,
    baseline_question_ids: list[str],
) -> None:
    if not verify_ciderqa_report(report):
        raise ValueError(f"{configuration.variant}: invalid CiderQA report signature")
    expected_common = (
        plan.dataset_version,
        plan.dataset_sha256,
        plan.split,
        plan.mode,
        plan.corpus_sha256,
        plan.code_revision,
        plan.model_versions,
        plan.seeds,
    )
    observed_common = (
        report.dataset_version,
        report.dataset_sha256,
        report.context.split,
        report.context.mode,
        report.context.corpus_sha256,
        report.context.code_revision,
        report.context.model_versions,
        report.context.seeds,
    )
    if observed_common != expected_common:
        raise ValueError(f"{configuration.variant}: run context differs from ablation plan")
    question_ids = [result.question_id for result in report.results]
    if len(question_ids) != len(set(question_ids)) or question_ids != baseline_question_ids:
        raise ValueError(f"{configuration.variant}: question set or order differs from baseline")
    parameters = report.context.parameters
    if parameters.get("ablation_plan_sha256") != plan.plan_sha256:
        raise ValueError(f"{configuration.variant}: missing signed ablation plan reference")
    for name, expected in configuration.signed_parameters.items():
        if parameters.get(name) != expected:
            raise ValueError(f"{configuration.variant}: signed parameter {name} is inconsistent")


def build_ablation_report(
    plan: CiderQAAblationPlan,
    reports: dict[str, SignedCiderQAReport],
    *,
    created_at: datetime | None = None,
) -> SignedCiderQAAblationReport:
    if not verify_ablation_plan(plan):
        raise ValueError("invalid CiderQA ablation plan")
    expected_variants = set(ABLATION_VARIANTS)
    if set(reports) != expected_variants:
        missing = sorted(expected_variants.difference(reports))
        extra = sorted(set(reports).difference(expected_variants))
        raise ValueError(
            f"ablation reports must match fixed matrix; missing={missing}, extra={extra}"
        )

    baseline = reports["baseline"]
    baseline_question_ids = [result.question_id for result in baseline.results]
    baseline_metrics = metrics_from_report(baseline)
    baseline_values = baseline_metrics.model_dump()
    comparisons: list[AblationComparison] = []
    for configuration in plan.configurations:
        source = reports[configuration.variant]
        _validate_source_report(
            plan=plan,
            configuration=configuration,
            report=source,
            baseline_question_ids=baseline_question_ids,
        )
        metrics = metrics_from_report(source)
        metric_values = metrics.model_dump()
        comparisons.append(
            AblationComparison(
                variant=configuration.variant,
                enabled_stage=(
                    None if configuration.variant == "baseline" else configuration.variant
                ),
                configuration_sha256=configuration.sha256,
                source_report_sha256=source.report_sha256,
                metrics=metrics,
                delta_from_baseline={
                    name: metric_values[name] - baseline_values[name]
                    for name in PromotionMetrics.model_fields
                },
                duration_delta_seconds=(
                    source.context.duration_seconds - baseline.context.duration_seconds
                ),
                peak_process_rss_delta_gb=(
                    source.context.peak_process_rss_gb - baseline.context.peak_process_rss_gb
                ),
                peak_system_used_delta_gb=(
                    source.context.peak_system_used_gb - baseline.context.peak_system_used_gb
                ),
                argo_request_delta=(
                    source.context.argo_requests_used - baseline.context.argo_requests_used
                ),
                argo_cost_delta_eur=source.context.argo_cost_eur - baseline.context.argo_cost_eur,
            )
        )

    result = SignedCiderQAAblationReport(
        created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
        plan_sha256=plan.plan_sha256,
        dataset_sha256=plan.dataset_sha256,
        split=plan.split,
        mode=plan.mode,
        baseline_report_sha256=baseline.report_sha256,
        comparisons=comparisons,
        report_sha256="0" * 64,
    )
    return result.model_copy(update={"report_sha256": _content_hash(result, "report_sha256")})


def verify_ablation_report(report: SignedCiderQAAblationReport) -> bool:
    return _content_hash(report, "report_sha256") == report.report_sha256


def write_signed_model(model: BaseModel, destination: str | Path) -> Path:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target
