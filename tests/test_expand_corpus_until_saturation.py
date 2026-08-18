from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.updates.harvest import BulkHarvestReport
from scripts.expand_corpus_until_saturation import (
    _campaign_deadline,
    _latest_retry_at,
    _next_checkpoint_retry,
    _next_error_count,
    _next_no_gain_count,
    _profile_checkpoint_is_terminal,
    _profile_state,
    _retry_at_for_messages,
    _source_checkpoint_is_blocked,
    _weekly_closure_due,
    build_parser,
)


def _report(*, stop_reason: str, errors: list[list[dict[str, str]]]) -> BulkHarvestReport:
    return BulkHarvestReport.model_construct(
        profile="expand_focused_crossref_v1",
        target_new_accepted_abstracts=10_000,
        baseline_accepted_abstracts=100,
        final_accepted_abstracts=100,
        new_accepted_abstracts=0,
        target_reached=False,
        stop_reason=stop_reason,
        harvest_runs=[SimpleNamespace(errors=value) for value in errors],
        backfill_runs=[],
    )


def test_expansion_parser_accepts_bounded_campaign_options() -> None:
    arguments = build_parser().parse_args(
        [
            "--sources",
            "openalex",
            "clarivate",
            "--query-sets",
            "materials",
            "microbiology",
            "--timeout-hours",
            "6",
        ]
    )

    assert arguments.sources == ["openalex", "clarivate"]
    assert arguments.query_sets == ["materials", "microbiology"]
    assert arguments.timeout_hours == 6
    assert arguments.wait_for_retries
    assert arguments.max_runs_per_profile == 1
    assert not arguments.reset_deadline


def test_expansion_parser_allows_an_explicit_new_timeout_window() -> None:
    arguments = build_parser().parse_args(["--reset-deadline", "--timeout-hours", "8"])

    assert arguments.reset_deadline
    assert arguments.timeout_hours == 8


def test_profile_state_distinguishes_saturation_from_provider_limit() -> None:
    saturated = _report(stop_reason="no_progress", errors=[[], [], [], []])
    retry = "2026-08-12T18:18:25+00:00"
    limited = _report(
        stop_reason="no_progress",
        errors=[
            [{"message": f"HTTP 429; retry_at={retry}"}],
            [{"message": f"HTTP 429; retry_at={retry}"}],
        ],
    )

    assert _profile_state(saturated, 2) == ("saturated", None)
    assert _profile_state(limited, 2) == ("limited", retry)


def test_profile_state_prioritizes_two_terminal_provider_errors_after_progress() -> None:
    retry = "2026-08-12T18:18:25+00:00"
    report = _report(
        stop_reason="no_progress",
        errors=[
            [],
            [],
            [{"message": f"HTTP 429; retry_at={retry}"}],
            [{"message": f"HTTP 429; retry_at={retry}"}],
        ],
    )

    assert _profile_state(report, 4) == ("limited", retry)


def test_single_daily_budget_error_remains_active_with_a_retry_window() -> None:
    report = _report(
        stop_reason="max_runs",
        errors=[[{"message": "OpenAlex free daily budget is insufficient for this run"}]],
    )

    state, retry_at = _profile_state(report, 4)

    assert state == "active"
    assert retry_at is not None and retry_at.endswith("T00:00:00+00:00")


def test_latest_retry_at_selects_the_safest_provider_time() -> None:
    assert (
        _latest_retry_at(
            [
                "retry_at=2026-08-12T18:00:00+00:00",
                "retry_at=2026-08-12T19:00:00+00:00",
            ]
        )
        == "2026-08-12T19:00:00+00:00"
    )


def test_expired_provider_limits_are_resumable_from_the_same_checkpoint() -> None:
    now = datetime(2026, 8, 12, 20, tzinfo=UTC)
    expired = "2026-08-12T19:00:00+00:00"
    future = "2026-08-12T21:00:00+00:00"

    assert not _source_checkpoint_is_blocked(f"limited_until_{expired}", now)
    assert _source_checkpoint_is_blocked(f"limited_until_{future}", now)
    assert not _profile_checkpoint_is_terminal({"state": "limited", "retry_at": expired}, now)
    assert _profile_checkpoint_is_terminal({"state": "limited", "retry_at": future}, now)


def test_weekly_no_gain_closure_skips_only_until_its_reopen_time() -> None:
    now = datetime(2026, 8, 12, 20, tzinfo=UTC)
    closed_until = "2026-08-19T20:00:00+00:00"
    profile = {
        "state": "closed_weekly",
        "retry_at": closed_until,
        "closure_reason": "no new accepted abstract after two thematic rotations",
    }

    assert _profile_checkpoint_is_terminal(profile, now)
    assert not _profile_checkpoint_is_terminal(
        profile,
        datetime(2026, 8, 19, 20, tzinfo=UTC),
    )


def test_missing_credential_source_is_rechecked_after_restart() -> None:
    now = datetime(2026, 8, 12, 20, tzinfo=UTC)

    assert not _source_checkpoint_is_blocked("unavailable_missing_credential", now)


def test_daily_openalex_budget_resumes_at_next_utc_midnight() -> None:
    now = datetime(2026, 8, 12, 20, 30, tzinfo=UTC)
    retry_at = "2026-08-13T00:00:00+00:00"
    message = "OpenAlex free daily budget is insufficient for this run"

    assert _retry_at_for_messages([message], now=now) == retry_at
    assert _profile_checkpoint_is_terminal(
        {
            "state": "limited",
            "retry_at": None,
            "updated_at": now.isoformat(),
            "report": {
                "harvest_runs": [
                    {"errors": [{"message": message}]},
                    {"errors": [{"message": message}]},
                ]
            },
        },
        now,
    )
    assert not _profile_checkpoint_is_terminal(
        {
            "state": "limited",
            "retry_at": None,
            "updated_at": now.isoformat(),
            "report": {
                "harvest_runs": [
                    {"errors": [{"message": message}]},
                    {"errors": [{"message": message}]},
                ]
            },
        },
        datetime(2026, 8, 13, 0, 1, tzinfo=UTC),
    )
    assert not _profile_checkpoint_is_terminal(
        {
            "state": "saturated",
            "retry_at": None,
            "updated_at": now.isoformat(),
            "report": {
                "harvest_runs": [
                    {"errors": []},
                    {"errors": [{"message": message}]},
                    {"errors": [{"message": message}]},
                ]
            },
        },
        datetime(2026, 8, 13, 0, 1, tzinfo=UTC),
    )


def test_unqualified_source_limit_defers_to_profile_checkpoints() -> None:
    now = datetime(2026, 8, 12, 20, tzinfo=UTC)

    assert not _source_checkpoint_is_blocked("limited", now)


def test_next_checkpoint_retry_selects_earliest_provider_window() -> None:
    now = datetime(2026, 8, 12, 20, tzinfo=UTC)
    profiles = {
        "saturated": {"state": "saturated", "retry_at": None, "report": {}},
        "later": {
            "state": "limited",
            "retry_at": "2026-08-13T01:00:00+00:00",
        },
        "earlier": {
            "state": "limited",
            "retry_at": "2026-08-13T00:00:00+00:00",
        },
        "weekly": {
            "state": "closed_weekly",
            "retry_at": "2026-08-19T20:00:00+00:00",
        },
    }

    assert _next_checkpoint_retry(profiles, now) == datetime(2026, 8, 13, 0, tzinfo=UTC)


def test_campaign_resume_preserves_the_original_deadline() -> None:
    resumed_at = datetime(2026, 8, 12, 22, tzinfo=UTC)
    original = "2026-08-13T04:00:00+00:00"

    assert _campaign_deadline({"deadline": original}, resumed_at, 10).isoformat() == original
    assert _campaign_deadline({}, resumed_at, 10) == datetime(2026, 8, 13, 8, tzinfo=UTC)


def test_no_gain_counter_persists_across_round_robin_profile_visits() -> None:
    no_gain = _report(stop_reason="max_runs", errors=[[]])
    gain = no_gain.model_copy(update={"new_accepted_abstracts": 2})

    assert _next_no_gain_count({}, no_gain) == 1
    assert _next_no_gain_count({"consecutive_no_gain_runs": 1}, no_gain) == 2
    assert _next_no_gain_count({"consecutive_no_gain_runs": 20}, gain) == 0


def test_provider_error_counter_is_distinct_from_scientific_no_gain() -> None:
    error = _report(
        stop_reason="max_runs",
        errors=[[{"message": "OpenAlex free daily budget is insufficient"}]],
    )

    assert _next_no_gain_count({"consecutive_no_gain_runs": 4}, error) == 4
    assert _next_error_count({}, error) == 1
    assert _next_error_count({"consecutive_error_runs": 1}, error) == 2


def test_weekly_closure_requires_two_complete_query_family_rotations() -> None:
    assert not _weekly_closure_due(7, wave_count=4)
    assert _weekly_closure_due(8, wave_count=4)
    with pytest.raises(ValueError, match="wave count must be positive"):
        _weekly_closure_due(1, wave_count=0)


def test_persisted_error_streak_with_one_current_report_is_resumable() -> None:
    message = "OpenAlex free daily budget is insufficient for this run"
    profile = {
        "state": "limited",
        "retry_at": None,
        "consecutive_error_runs": 2,
        "updated_at": "2026-08-12T20:00:00+00:00",
        "report": {"harvest_runs": [{"errors": [{"message": message}]}]},
    }

    assert not _profile_checkpoint_is_terminal(profile, datetime(2026, 8, 13, 0, 1, tzinfo=UTC))
