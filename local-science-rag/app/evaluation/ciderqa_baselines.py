"""Comparable signed abstract-only and full-text CiderQA baselines."""

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
    SignedCiderQAReport,
    canonical_json,
    verify_ciderqa_report,
)


class SignedCiderQABaselineComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    created_at: datetime
    dataset_version: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: CiderQASplit
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_revision: str
    abstract_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    full_text_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    abstract_metrics: PromotionMetrics
    full_text_metrics: PromotionMetrics
    full_text_delta: dict[str, float]
    duration_delta_seconds: float
    peak_process_rss_delta_gb: float
    peak_system_used_delta_gb: float
    argo_request_delta: int
    argo_cost_delta_eur: float
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _content_hash(report: SignedCiderQABaselineComparison) -> str:
    payload = report.model_dump(mode="json", exclude={"report_sha256"})
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_baseline_comparison(
    abstract_only: SignedCiderQAReport,
    full_text: SignedCiderQAReport,
    *,
    created_at: datetime | None = None,
) -> SignedCiderQABaselineComparison:
    if not verify_ciderqa_report(abstract_only) or not verify_ciderqa_report(full_text):
        raise ValueError("CiderQA baseline source signature is invalid")
    if abstract_only.context.mode != "abstract_only" or full_text.context.mode != "full_text":
        raise ValueError("baseline comparison requires abstract_only then full_text")
    abstract_ids = [result.question_id for result in abstract_only.results]
    full_text_ids = [result.question_id for result in full_text.results]
    common_abstract = (
        abstract_only.dataset_version,
        abstract_only.dataset_sha256,
        abstract_only.question_count,
        abstract_only.context.split,
        abstract_only.context.corpus_sha256,
        abstract_only.context.code_revision,
        abstract_only.context.model_versions,
        abstract_only.context.seeds,
        abstract_ids,
    )
    common_full_text = (
        full_text.dataset_version,
        full_text.dataset_sha256,
        full_text.question_count,
        full_text.context.split,
        full_text.context.corpus_sha256,
        full_text.context.code_revision,
        full_text.context.model_versions,
        full_text.context.seeds,
        full_text_ids,
    )
    if common_abstract != common_full_text:
        raise ValueError("CiderQA baselines differ outside their evidence mode")
    abstract_metrics = metrics_from_report(abstract_only)
    full_text_metrics = metrics_from_report(full_text)
    abstract_values = abstract_metrics.model_dump()
    full_text_values = full_text_metrics.model_dump()
    report = SignedCiderQABaselineComparison(
        created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
        dataset_version=abstract_only.dataset_version,
        dataset_sha256=abstract_only.dataset_sha256,
        split=abstract_only.context.split,
        corpus_sha256=abstract_only.context.corpus_sha256,
        code_revision=abstract_only.context.code_revision,
        abstract_report_sha256=abstract_only.report_sha256,
        full_text_report_sha256=full_text.report_sha256,
        abstract_metrics=abstract_metrics,
        full_text_metrics=full_text_metrics,
        full_text_delta={
            name: full_text_values[name] - abstract_values[name]
            for name in PromotionMetrics.model_fields
        },
        duration_delta_seconds=(
            full_text.context.duration_seconds - abstract_only.context.duration_seconds
        ),
        peak_process_rss_delta_gb=(
            full_text.context.peak_process_rss_gb - abstract_only.context.peak_process_rss_gb
        ),
        peak_system_used_delta_gb=(
            full_text.context.peak_system_used_gb - abstract_only.context.peak_system_used_gb
        ),
        argo_request_delta=(
            full_text.context.argo_requests_used - abstract_only.context.argo_requests_used
        ),
        argo_cost_delta_eur=(full_text.context.argo_cost_eur - abstract_only.context.argo_cost_eur),
        report_sha256="0" * 64,
    )
    return report.model_copy(update={"report_sha256": _content_hash(report)})


def verify_baseline_comparison(report: SignedCiderQABaselineComparison) -> bool:
    return _content_hash(report) == report.report_sha256


def write_baseline_comparison(
    report: SignedCiderQABaselineComparison,
    destination: str | Path,
) -> Path:
    if not verify_baseline_comparison(report):
        raise ValueError("cannot write an invalid CiderQA baseline comparison")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target
