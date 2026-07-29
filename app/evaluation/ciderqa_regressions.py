"""Turn classified real CiderQA baseline failures into replayable gates."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.ciderqa import CiderQASplit
from app.evaluation.ciderqa_report import (
    SignedCiderQAReport,
    canonical_json,
    verify_ciderqa_report,
)

RegressionCategory = Literal[
    "negation",
    "unit",
    "population",
    "page",
    "source",
    "forced_answer",
]

REGRESSION_CATEGORIES: tuple[RegressionCategory, ...] = (
    "negation",
    "unit",
    "population",
    "page",
    "source",
    "forced_answer",
)


class RegressionClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: RegressionCategory
    question_id: str = Field(min_length=3, max_length=100)
    rationale: str = Field(min_length=10, max_length=1000)


class CiderQARegressionCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: RegressionCategory
    question_id: str
    rationale: str
    baseline_value: float = Field(ge=0, le=1)
    required_value: float = Field(ge=0, le=1)


class SignedCiderQARegressionPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    created_at: datetime
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: CiderQASplit
    source_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: list[CiderQARegressionCase] = Field(min_length=6, max_length=6)
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def complete_unique_matrix(self) -> SignedCiderQARegressionPackage:
        if {case.category for case in self.cases} != set(REGRESSION_CATEGORIES):
            raise ValueError("regression package requires every fixed error category exactly once")
        if len({case.question_id for case in self.cases}) != len(self.cases):
            raise ValueError("representative regression cases must use distinct questions")
        return self


class RegressionReplayResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: RegressionCategory
    question_id: str
    observed_value: float = Field(ge=0, le=1)
    required_value: float = Field(ge=0, le=1)
    passed: bool


class SignedRegressionReplayReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    created_at: datetime
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    results: list[RegressionReplayResult] = Field(min_length=6, max_length=6)
    passed: bool
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _hash(model: BaseModel, field: str) -> str:
    payload = model.model_dump(mode="json", exclude={field})
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _case_value(
    report: SignedCiderQAReport, category: RegressionCategory, question_id: str
) -> float:
    by_id = {case.question_id: case for case in report.metrics.cases}
    try:
        case = by_id[question_id]
    except KeyError as error:
        raise ValueError(f"unknown CiderQA question {question_id}") from error
    if category in {"negation", "unit", "population"}:
        values = [case.exactness, case.completeness]
        if any(value is None for value in values):
            raise ValueError(f"{question_id} has no factual answer to classify")
        return min(float(value) for value in values if value is not None)
    if category == "page":
        if case.page_accuracy is None:
            raise ValueError(f"{question_id} has no page assessment")
        return case.page_accuracy
    if category == "source":
        if case.citation_precision is None:
            raise ValueError(f"{question_id} has no source assessment")
        return case.citation_precision
    if case.answerable:
        raise ValueError(f"{question_id} is not an unanswerable forced-answer case")
    return float(not case.answered)


def build_regression_package(
    source_report: SignedCiderQAReport,
    classifications: list[RegressionClassification],
    *,
    created_at: datetime | None = None,
) -> SignedCiderQARegressionPackage:
    if not verify_ciderqa_report(source_report):
        raise ValueError("source CiderQA baseline signature is invalid")
    if len(classifications) != 6:
        raise ValueError("exactly six representative classifications are required")
    cases: list[CiderQARegressionCase] = []
    for classification in classifications:
        baseline_value = _case_value(
            source_report,
            classification.category,
            classification.question_id,
        )
        if baseline_value >= 1:
            raise ValueError(
                f"{classification.question_id} does not exhibit {classification.category}"
            )
        cases.append(
            CiderQARegressionCase(
                **classification.model_dump(),
                baseline_value=baseline_value,
                required_value=1.0,
            )
        )
    package = SignedCiderQARegressionPackage(
        created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
        dataset_sha256=source_report.dataset_sha256,
        split=source_report.context.split,
        source_report_sha256=source_report.report_sha256,
        cases=cases,
        package_sha256="0" * 64,
    )
    return package.model_copy(update={"package_sha256": _hash(package, "package_sha256")})


def verify_regression_package(package: SignedCiderQARegressionPackage) -> bool:
    return _hash(package, "package_sha256") == package.package_sha256


def replay_regression_package(
    package: SignedCiderQARegressionPackage,
    candidate: SignedCiderQAReport,
    *,
    created_at: datetime | None = None,
) -> SignedRegressionReplayReport:
    if not verify_regression_package(package) or not verify_ciderqa_report(candidate):
        raise ValueError("regression package or candidate signature is invalid")
    if (
        package.dataset_sha256 != candidate.dataset_sha256
        or package.split != candidate.context.split
    ):
        raise ValueError("regression package and candidate use different CiderQA data")
    results = [
        RegressionReplayResult(
            category=case.category,
            question_id=case.question_id,
            observed_value=_case_value(candidate, case.category, case.question_id),
            required_value=case.required_value,
            passed=(_case_value(candidate, case.category, case.question_id) >= case.required_value),
        )
        for case in package.cases
    ]
    report = SignedRegressionReplayReport(
        created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
        package_sha256=package.package_sha256,
        candidate_report_sha256=candidate.report_sha256,
        results=results,
        passed=all(result.passed for result in results),
        report_sha256="0" * 64,
    )
    return report.model_copy(update={"report_sha256": _hash(report, "report_sha256")})


def write_regression_artifact(model: BaseModel, destination: str | Path) -> Path:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target
