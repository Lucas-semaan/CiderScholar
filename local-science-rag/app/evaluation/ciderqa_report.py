"""Reproducible, content-addressed CiderQA evaluation reports."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.evaluation.ciderqa import CiderQASplit, CiderQASplitDataset
from app.evaluation.ciderqa_metrics import (
    CiderQAInferenceResult,
    CiderQAMetricsReport,
    evaluate_ciderqa_results,
)

JsonScalar = str | int | float | bool | None


class CiderQAResultSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    results: list[CiderQAInferenceResult] = Field(min_length=1)


class CiderQARunContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    split: CiderQASplit
    mode: Literal["abstract_only", "full_text"]
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_revision: str = Field(min_length=7, max_length=64)
    model_versions: dict[str, str] = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameters: dict[str, JsonScalar]
    seeds: dict[str, int] = Field(min_length=1)
    started_at: datetime
    completed_at: datetime
    duration_seconds: float = Field(ge=0.0)
    peak_process_rss_gb: float = Field(ge=0.0)
    peak_system_used_gb: float = Field(ge=0.0)
    argo_authorized: bool = False
    argo_request_limit: int = Field(default=0, ge=0)
    argo_requests_used: int = Field(default=0, ge=0)
    argo_prompt_tokens: int = Field(default=0, ge=0)
    argo_completion_tokens: int = Field(default=0, ge=0)
    argo_cost_eur: float = Field(default=0.0, ge=0.0)

    @field_validator("started_at", "completed_at")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("CiderQA timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def bounded_external_usage(self) -> CiderQARunContext:
        if self.completed_at < self.started_at:
            raise ValueError("CiderQA completion cannot precede its start")
        observed_duration = (self.completed_at - self.started_at).total_seconds()
        if abs(observed_duration - self.duration_seconds) > 1.0:
            raise ValueError("CiderQA duration does not match its timestamps")
        if not self.argo_authorized and any(
            (
                self.argo_request_limit,
                self.argo_requests_used,
                self.argo_prompt_tokens,
                self.argo_completion_tokens,
                self.argo_cost_eur,
            )
        ):
            raise ValueError("ARGO usage requires explicit authorization")
        if self.argo_requests_used > self.argo_request_limit:
            raise ValueError("ARGO usage exceeds the explicit request budget")
        if self.argo_authorized and self.argo_request_limit < 1:
            raise ValueError("authorized ARGO use requires a positive request budget")
        return self


class SignedCiderQAReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    created_at: datetime
    dataset_version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    question_count: int = Field(ge=1)
    context: CiderQARunContext
    metrics: CiderQAMetricsReport
    results: list[CiderQAInferenceResult]
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload_hash(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_signed_ciderqa_report(
    dataset: CiderQASplitDataset,
    results: list[CiderQAInferenceResult],
    context: CiderQARunContext,
    *,
    dataset_version: str,
    dataset_sha256: str,
    created_at: datetime | None = None,
) -> SignedCiderQAReport:
    if dataset.split != context.split:
        raise ValueError("CiderQA dataset and run context use different splits")
    metrics = evaluate_ciderqa_results(dataset, results)
    unsigned_report = SignedCiderQAReport(
        schema_version=1,
        created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
        dataset_version=dataset_version,
        dataset_sha256=dataset_sha256,
        question_count=len(dataset.questions),
        context=context,
        metrics=metrics,
        results=results,
        report_sha256="0" * 64,
    )
    payload = unsigned_report.model_dump(mode="json", exclude={"report_sha256"})
    return unsigned_report.model_copy(update={"report_sha256": _payload_hash(payload)})


def verify_ciderqa_report(report: SignedCiderQAReport) -> bool:
    payload = report.model_dump(mode="json", exclude={"report_sha256"})
    return _payload_hash(payload) == report.report_sha256


def write_ciderqa_report(report: SignedCiderQAReport, destination: str | Path) -> Path:
    if not verify_ciderqa_report(report):
        raise ValueError("CiderQA report signature is invalid")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target
