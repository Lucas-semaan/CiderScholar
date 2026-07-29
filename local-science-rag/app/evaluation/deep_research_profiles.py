"""Signed physical-host validation reports for the 8 GB and 16 GB profiles."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.ciderqa_report import canonical_json

ProfileName = Literal["8gb", "16gb"]


class ProfileCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["resume", "cancellation", "cache_private", "no_leak"]
    test_node_id: str = Field(min_length=3, max_length=500)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    passed: bool
    duration_seconds: float = Field(ge=0)
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SignedProfileTrialReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    created_at: datetime
    profile: ProfileName
    detected_total_memory_gb: float = Field(gt=0)
    physical_memory_match: bool
    platform: str = Field(min_length=3, max_length=300)
    python_version: str = Field(min_length=3, max_length=50)
    host_fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_revision: str = Field(min_length=7, max_length=64)
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    peak_test_process_rss_gb: float = Field(ge=0)
    peak_system_used_gb: float = Field(ge=0)
    checks: list[ProfileCheckResult] = Field(min_length=4, max_length=4)
    passed: bool
    failures: list[str]
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def fixed_check_matrix(self) -> SignedProfileTrialReport:
        expected = {"resume", "cancellation", "cache_private", "no_leak"}
        if {check.name for check in self.checks} != expected:
            raise ValueError("physical profile report requires the fixed four-check matrix")
        if self.passed != (not self.failures):
            raise ValueError("physical profile pass state must match its failures")
        return self


class SignedDualProfileReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    created_at: datetime
    code_revision: str = Field(min_length=7, max_length=64)
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    eight_gb_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sixteen_gb_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    passed: Literal[True]
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _content_hash(model: BaseModel, field: str) -> str:
    payload = model.model_dump(mode="json", exclude={field})
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def profile_memory_matches(profile: ProfileName, detected_gb: float) -> bool:
    lower, upper = (6.5, 10.0) if profile == "8gb" else (13.0, 20.0)
    return lower <= detected_gb <= upper


def build_profile_trial_report(
    *,
    profile: ProfileName,
    detected_total_memory_gb: float,
    platform: str,
    python_version: str,
    host_fingerprint_sha256: str,
    code_revision: str,
    corpus_sha256: str,
    peak_test_process_rss_gb: float,
    peak_system_used_gb: float,
    checks: list[ProfileCheckResult],
    created_at: datetime | None = None,
) -> SignedProfileTrialReport:
    failures: list[str] = []
    memory_match = profile_memory_matches(profile, detected_total_memory_gb)
    if not memory_match:
        failures.append(
            f"physical memory {detected_total_memory_gb:.2f} GB does not match {profile}"
        )
    maximum_process = 5.0 if profile == "8gb" else 12.5
    maximum_system = 6.0 if profile == "8gb" else 13.0
    if peak_test_process_rss_gb > maximum_process:
        failures.append(
            f"peak process RSS {peak_test_process_rss_gb:.2f} GB exceeds {maximum_process:.2f} GB"
        )
    if peak_system_used_gb > maximum_system:
        failures.append(
            f"peak system use {peak_system_used_gb:.2f} GB exceeds {maximum_system:.2f} GB"
        )
    failures.extend(f"{check.name} check failed" for check in checks if not check.passed)
    report = SignedProfileTrialReport(
        created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
        profile=profile,
        detected_total_memory_gb=detected_total_memory_gb,
        physical_memory_match=memory_match,
        platform=platform,
        python_version=python_version,
        host_fingerprint_sha256=host_fingerprint_sha256,
        code_revision=code_revision,
        corpus_sha256=corpus_sha256,
        peak_test_process_rss_gb=peak_test_process_rss_gb,
        peak_system_used_gb=peak_system_used_gb,
        checks=checks,
        passed=not failures,
        failures=failures,
        report_sha256="0" * 64,
    )
    return report.model_copy(update={"report_sha256": _content_hash(report, "report_sha256")})


def verify_profile_trial_report(report: SignedProfileTrialReport) -> bool:
    return _content_hash(report, "report_sha256") == report.report_sha256


def build_dual_profile_report(
    eight_gb: SignedProfileTrialReport,
    sixteen_gb: SignedProfileTrialReport,
    *,
    created_at: datetime | None = None,
) -> SignedDualProfileReport:
    if not verify_profile_trial_report(eight_gb) or not verify_profile_trial_report(sixteen_gb):
        raise ValueError("physical profile source signature is invalid")
    if eight_gb.profile != "8gb" or sixteen_gb.profile != "16gb":
        raise ValueError("both physical profiles are required in their canonical order")
    if not eight_gb.passed or not sixteen_gb.passed:
        raise ValueError("both physical profile trials must pass")
    if (
        eight_gb.code_revision != sixteen_gb.code_revision
        or eight_gb.corpus_sha256 != sixteen_gb.corpus_sha256
    ):
        raise ValueError("physical profile trials use different code or corpora")
    report = SignedDualProfileReport(
        created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
        code_revision=eight_gb.code_revision,
        corpus_sha256=eight_gb.corpus_sha256,
        eight_gb_report_sha256=eight_gb.report_sha256,
        sixteen_gb_report_sha256=sixteen_gb.report_sha256,
        passed=True,
        report_sha256="0" * 64,
    )
    return report.model_copy(update={"report_sha256": _content_hash(report, "report_sha256")})


def verify_dual_profile_report(report: SignedDualProfileReport) -> bool:
    return _content_hash(report, "report_sha256") == report.report_sha256


def write_profile_report(report: BaseModel, destination: str | Path) -> Path:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target
