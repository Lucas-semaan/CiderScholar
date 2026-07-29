from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.evaluation.deep_research_profiles import (
    ProfileCheckResult,
    build_dual_profile_report,
    build_profile_trial_report,
    verify_dual_profile_report,
    verify_profile_trial_report,
)


def _checks() -> list[ProfileCheckResult]:
    return [
        ProfileCheckResult(
            name=name,
            test_node_id=f"tests/test.py::test_{name}",
            source_sha256="a" * 64,
            passed=True,
            duration_seconds=1,
            output_sha256="b" * 64,
        )
        for name in ("resume", "cancellation", "cache_private", "no_leak")
    ]


def _trial(profile: str, memory: float):
    return build_profile_trial_report(
        profile=profile,
        detected_total_memory_gb=memory,
        platform="Windows-11-test",
        python_version="3.12.10",
        host_fingerprint_sha256="c" * 64,
        code_revision="abcdef123456",
        corpus_sha256="d" * 64,
        peak_test_process_rss_gb=1.0,
        peak_system_used_gb=5.0 if profile == "8gb" else 10.0,
        checks=_checks(),
        created_at=datetime(2026, 7, 27, tzinfo=UTC),
    )


def test_two_real_profile_reports_are_required_and_content_addressed() -> None:
    eight = _trial("8gb", 7.8)
    sixteen = _trial("16gb", 15.7)

    assert verify_profile_trial_report(eight)
    assert verify_profile_trial_report(sixteen)
    combined = build_dual_profile_report(eight, sixteen)
    assert verify_dual_profile_report(combined)
    assert combined.passed is True


def test_wrong_physical_memory_or_failed_check_blocks_finalization() -> None:
    wrong_host = _trial("8gb", 15.7)
    assert wrong_host.passed is False
    assert wrong_host.physical_memory_match is False

    with pytest.raises(ValueError, match="must pass"):
        build_dual_profile_report(wrong_host, _trial("16gb", 15.7))
