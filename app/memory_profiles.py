"""Explicit memory profiles; selection is always visible and user-controlled."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.config import Settings


class MemoryProfileName(StrEnum):
    EIGHT_GB = "8gb"
    SIXTEEN_GB = "16gb"


@dataclass(frozen=True, slots=True)
class MemoryProfile:
    name: MemoryProfileName
    total_memory_gb: int
    warning_used_gb: float
    hard_process_limit_gb: float
    minimum_available_mb: int
    embedding_batch_size: int
    hybrid_candidate_limit: int
    evidence_candidate_chunks: int
    reranker_batch_size: int
    reranker_candidate_limit: int


@dataclass(frozen=True, slots=True)
class MemoryProfileRecommendation:
    detected_total_gb: float | None
    recommended_profile: MemoryProfileName | None
    active_profile: str
    applied_automatically: bool = False


EIGHT_GB_PROFILE = MemoryProfile(
    name=MemoryProfileName.EIGHT_GB,
    total_memory_gb=8,
    warning_used_gb=6.0,
    hard_process_limit_gb=5.0,
    minimum_available_mb=1024,
    embedding_batch_size=2,
    hybrid_candidate_limit=80,
    evidence_candidate_chunks=50,
    reranker_batch_size=2,
    reranker_candidate_limit=40,
)

SIXTEEN_GB_PROFILE = MemoryProfile(
    name=MemoryProfileName.SIXTEEN_GB,
    total_memory_gb=16,
    warning_used_gb=13.0,
    hard_process_limit_gb=12.5,
    minimum_available_mb=1024,
    embedding_batch_size=12,
    hybrid_candidate_limit=200,
    evidence_candidate_chunks=100,
    reranker_batch_size=4,
    reranker_candidate_limit=80,
)

MEMORY_PROFILES = {profile.name: profile for profile in (EIGHT_GB_PROFILE, SIXTEEN_GB_PROFILE)}


def apply_memory_profile(settings: Settings, profile: MemoryProfile) -> Settings:
    """Return validated settings without mutating persisted configuration."""

    payload = settings.model_dump(mode="python")
    payload["memory"].update(
        {
            "profile": profile.name.value,
            "warning_used_gb": profile.warning_used_gb,
            "hard_process_limit_gb": profile.hard_process_limit_gb,
            "minimum_available_mb": profile.minimum_available_mb,
        }
    )
    payload["embeddings"]["batch_size"] = profile.embedding_batch_size
    payload["retrieval"]["hybrid_candidate_limit"] = profile.hybrid_candidate_limit
    payload["evidence"]["candidate_chunks_per_article"] = profile.evidence_candidate_chunks
    payload["reranker"]["batch_size"] = profile.reranker_batch_size
    payload["reranker"]["candidate_limit"] = profile.reranker_candidate_limit
    payload["deep_research"]["rrf_candidate_limit"] = profile.reranker_candidate_limit
    payload["deep_research"]["cross_encoder_candidate_limit"] = min(
        profile.reranker_candidate_limit,
        40,
    )
    return Settings.model_validate(payload)


def detect_total_memory_gb() -> float | None:
    try:
        import psutil
    except ImportError:
        return None
    return round(psutil.virtual_memory().total / (1024**3), 1)


def recommend_memory_profile(
    settings: Settings,
    *,
    detected_total_gb: float | None = None,
) -> MemoryProfileRecommendation:
    """Recommend a profile without applying or persisting it."""

    detected = detect_total_memory_gb() if detected_total_gb is None else detected_total_gb
    recommended = None
    if detected is not None:
        recommended = (
            MemoryProfileName.EIGHT_GB if detected <= 12.0 else MemoryProfileName.SIXTEEN_GB
        )
    return MemoryProfileRecommendation(
        detected_total_gb=detected,
        recommended_profile=recommended,
        active_profile=settings.memory.profile,
    )
