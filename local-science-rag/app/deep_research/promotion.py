"""Fail-closed scientific and resource gate for public deep research."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.deep_research.cache import combined_corpus_fingerprint
from app.evaluation.ciderqa_ablation import (
    SignedCiderQAAblationReport,
    verify_ablation_report,
)
from app.evaluation.ciderqa_promotion import compare_signed_reports
from app.evaluation.ciderqa_report import (
    SignedCiderQAReport,
    canonical_json,
    verify_ciderqa_report,
)

PROMOTION_POLICY_VERSION = "1.0.0"
ACTIVATION_FILENAME = "deep-research-activation.json"


class ResourceThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_duration_seconds: float = 1800.0
    maximum_argo_requests: int = 8
    maximum_argo_cost_eur: float = 1.0
    maximum_process_rss_gb: float
    maximum_system_used_gb: float


RESOURCE_THRESHOLDS = {
    "8gb": ResourceThresholds(
        maximum_process_rss_gb=5.0,
        maximum_system_used_gb=6.0,
    ),
    "16gb": ResourceThresholds(
        maximum_process_rss_gb=12.5,
        maximum_system_used_gb=13.0,
    ),
}


class DeepResearchPromotionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version: Literal["1.0.0"] = PROMOTION_POLICY_VERSION
    promoted: bool
    memory_profile: Literal["8gb", "16gb"]
    failures: list[str]


class DeepResearchActivationBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    created_at: datetime
    baseline: SignedCiderQAReport
    candidate: SignedCiderQAReport
    ablation: SignedCiderQAAblationReport
    assessment: DeepResearchPromotionAssessment
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DeepResearchAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    state: Literal[
        "disabled",
        "missing_evaluation",
        "invalid_evaluation",
        "profile_mismatch",
        "corpus_mismatch",
        "ready",
    ]
    message: str
    bundle_sha256: str | None = None


def activation_bundle_path(settings: Settings) -> Path:
    return settings.paths.data_dir / "evaluation" / ACTIVATION_FILENAME


def _bundle_hash(bundle: DeepResearchActivationBundle) -> str:
    payload = bundle.model_dump(mode="json", exclude={"bundle_sha256"})
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def assess_deep_research_promotion(
    baseline: SignedCiderQAReport,
    candidate: SignedCiderQAReport,
    ablation: SignedCiderQAAblationReport,
    *,
    memory_profile: Literal["8gb", "16gb"],
) -> DeepResearchPromotionAssessment:
    failures: list[str] = []
    scientific = compare_signed_reports(baseline, candidate)
    failures.extend(scientific.failures)
    if not verify_ciderqa_report(baseline) or not verify_ciderqa_report(candidate):
        failures.append("source CiderQA report signature is invalid")
    if candidate.context.mode != "full_text":
        failures.append("candidate must be evaluated in full_text mode")
    if candidate.context.split != "validation":
        failures.append("candidate must be evaluated on the frozen validation split")
    if not verify_ablation_report(ablation):
        failures.append("ablation report signature is invalid")
    elif (
        ablation.dataset_sha256 != candidate.dataset_sha256
        or ablation.split != candidate.context.split
        or ablation.mode != candidate.context.mode
    ):
        failures.append("ablation and promotion candidate use different evaluation contexts")

    parameters = candidate.context.parameters
    required_stages = (
        "drs_query_variants_enabled",
        "drs_reranker_enabled",
        "drs_contextual_summary_enabled",
        "drs_iteration_enabled",
        "drs_citation_traversal_enabled",
    )
    for parameter in required_stages:
        if parameters.get(parameter) is not True:
            failures.append(f"candidate did not enable required stage {parameter}")
    calibration_hash = parameters.get("contextual_relevance_observations_sha256")
    if (
        not isinstance(calibration_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", calibration_hash) is None
    ):
        failures.append("candidate lacks a pinned contextual-relevance calibration hash")
    if parameters.get("memory_profile") != memory_profile:
        failures.append("candidate memory profile differs from promotion request")

    thresholds = RESOURCE_THRESHOLDS[memory_profile]
    resource_checks = {
        "duration_seconds": (
            candidate.context.duration_seconds,
            thresholds.maximum_duration_seconds,
        ),
        "argo_requests_used": (
            float(candidate.context.argo_requests_used),
            float(thresholds.maximum_argo_requests),
        ),
        "argo_cost_eur": (
            candidate.context.argo_cost_eur,
            thresholds.maximum_argo_cost_eur,
        ),
        "peak_process_rss_gb": (
            candidate.context.peak_process_rss_gb,
            thresholds.maximum_process_rss_gb,
        ),
        "peak_system_used_gb": (
            candidate.context.peak_system_used_gb,
            thresholds.maximum_system_used_gb,
        ),
    }
    for name, (observed, maximum) in resource_checks.items():
        if observed > maximum:
            failures.append(f"{name}: {observed:.4f} > maximum {maximum:.4f}")
    return DeepResearchPromotionAssessment(
        promoted=not failures,
        memory_profile=memory_profile,
        failures=list(dict.fromkeys(failures)),
    )


def build_activation_bundle(
    baseline: SignedCiderQAReport,
    candidate: SignedCiderQAReport,
    ablation: SignedCiderQAAblationReport,
    *,
    memory_profile: Literal["8gb", "16gb"],
    created_at: datetime | None = None,
) -> DeepResearchActivationBundle:
    assessment = assess_deep_research_promotion(
        baseline,
        candidate,
        ablation,
        memory_profile=memory_profile,
    )
    if not assessment.promoted:
        raise ValueError("deep-research promotion failed: " + "; ".join(assessment.failures))
    bundle = DeepResearchActivationBundle(
        created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
        baseline=baseline,
        candidate=candidate,
        ablation=ablation,
        assessment=assessment,
        bundle_sha256="0" * 64,
    )
    return bundle.model_copy(update={"bundle_sha256": _bundle_hash(bundle)})


def verify_activation_bundle(bundle: DeepResearchActivationBundle) -> bool:
    if _bundle_hash(bundle) != bundle.bundle_sha256:
        return False
    expected = assess_deep_research_promotion(
        bundle.baseline,
        bundle.candidate,
        bundle.ablation,
        memory_profile=bundle.assessment.memory_profile,
    )
    return expected == bundle.assessment and expected.promoted


def write_activation_bundle(
    bundle: DeepResearchActivationBundle,
    destination: str | Path,
) -> Path:
    if not verify_activation_bundle(bundle):
        raise ValueError("cannot write an invalid deep-research activation bundle")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def deep_research_availability(settings: Settings) -> DeepResearchAvailability:
    if not settings.deep_research.enabled:
        return DeepResearchAvailability(
            available=False,
            state="disabled",
            message="L’analyse approfondie n’est pas activée dans la configuration.",
        )
    path = activation_bundle_path(settings)
    if not path.is_file():
        return DeepResearchAvailability(
            available=False,
            state="missing_evaluation",
            message="Le bundle de promotion CiderQA est absent.",
        )
    try:
        bundle = DeepResearchActivationBundle.model_validate_json(path.read_text(encoding="utf-8"))
        valid = verify_activation_bundle(bundle)
    except (OSError, ValueError):
        valid = False
        bundle = None
    if not valid or bundle is None:
        return DeepResearchAvailability(
            available=False,
            state="invalid_evaluation",
            message="Le bundle de promotion CiderQA est invalide ou incomplet.",
        )
    if settings.memory.profile != bundle.assessment.memory_profile:
        return DeepResearchAvailability(
            available=False,
            state="profile_mismatch",
            message="Le profil mémoire actif n’est pas celui qui a franchi le gate.",
            bundle_sha256=bundle.bundle_sha256,
        )
    if combined_corpus_fingerprint(settings) != bundle.candidate.context.corpus_sha256:
        return DeepResearchAvailability(
            available=False,
            state="corpus_mismatch",
            message="Le corpus installé diffère du corpus évalué par CiderQA.",
            bundle_sha256=bundle.bundle_sha256,
        )
    return DeepResearchAvailability(
        available=True,
        state="ready",
        message="L’analyse approfondie a franchi le gate scientifique et de ressources.",
        bundle_sha256=bundle.bundle_sha256,
    )
