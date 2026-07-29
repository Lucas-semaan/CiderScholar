from __future__ import annotations

import logging

import pytest

from app.config import MemoryConfig
from app.memory import MemoryGuard, MemoryLimitError, MemorySnapshot
from app.memory_profiles import (
    EIGHT_GB_PROFILE,
    SIXTEEN_GB_PROFILE,
    apply_memory_profile,
    recommend_memory_profile,
)


def test_memory_guard_warns_without_stopping(monkeypatch, caplog) -> None:
    guard = MemoryGuard(MemoryConfig())
    snapshot = MemorySnapshot(
        process_rss_gb=0.5,
        system_used_gb=13.1,
        system_available_gb=2.0,
    )
    monkeypatch.setattr(guard, "snapshot", lambda: snapshot)

    with caplog.at_level(logging.WARNING):
        result = guard.check("synthetic operation")

    assert result == snapshot
    assert "synthetic operation" in caplog.text
    assert "system_used_gb=13.10" in caplog.text


@pytest.mark.parametrize(
    "snapshot",
    [
        MemorySnapshot(
            process_rss_gb=14.0,
            system_used_gb=14.5,
            system_available_gb=1.0,
        ),
        MemorySnapshot(
            process_rss_gb=0.5,
            system_used_gb=12.0,
            system_available_gb=0.49,
        ),
    ],
)
def test_memory_guard_stops_at_either_hard_limit(monkeypatch, snapshot: MemorySnapshot) -> None:
    guard = MemoryGuard(MemoryConfig())
    monkeypatch.setattr(guard, "snapshot", lambda: snapshot)

    with pytest.raises(MemoryLimitError, match="synthetic operation"):
        guard.check("synthetic operation")


def test_eight_gb_profile_uses_bounded_batches_and_machine_thresholds(settings) -> None:
    profiled = apply_memory_profile(settings, EIGHT_GB_PROFILE)

    assert profiled.memory.profile == "8gb"
    assert profiled.memory.warning_used_gb == 6.0
    assert profiled.memory.hard_process_limit_gb == 5.0
    assert profiled.memory.minimum_available_mb == 1024
    assert profiled.embeddings.batch_size == 2
    assert profiled.retrieval.hybrid_candidate_limit == 80
    assert profiled.evidence.candidate_chunks_per_article == 50
    assert profiled.reranker.batch_size == 2
    assert profiled.reranker.candidate_limit == 40
    assert profiled.deep_research.rrf_candidate_limit == 40
    assert profiled.deep_research.cross_encoder_candidate_limit == 40
    assert settings.memory.profile == "custom"


def test_sixteen_gb_profile_increases_batch_with_safe_headroom(settings) -> None:
    eight = apply_memory_profile(settings, EIGHT_GB_PROFILE)
    sixteen = apply_memory_profile(settings, SIXTEEN_GB_PROFILE)

    assert sixteen.memory.profile == "16gb"
    assert sixteen.embeddings.batch_size > eight.embeddings.batch_size
    assert sixteen.embeddings.batch_size == 12
    assert sixteen.memory.warning_used_gb == 13.0
    assert sixteen.memory.hard_process_limit_gb == 12.5
    assert sixteen.memory.minimum_available_mb == 1024
    assert sixteen.reranker.batch_size == 4
    assert sixteen.reranker.candidate_limit == 80
    assert sixteen.deep_research.rrf_candidate_limit == 80
    assert sixteen.deep_research.cross_encoder_candidate_limit == 40


@pytest.mark.parametrize(
    ("total_gb", "expected"),
    [(7.8, "8gb"), (8.0, "8gb"), (15.7, "16gb"), (32.0, "16gb")],
)
def test_memory_detection_recommends_without_applying(settings, total_gb, expected) -> None:
    recommendation = recommend_memory_profile(settings, detected_total_gb=total_gb)

    assert recommendation.recommended_profile == expected
    assert recommendation.active_profile == "custom"
    assert recommendation.applied_automatically is False
    assert settings.memory.profile == "custom"
