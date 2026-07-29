from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.admin.maintenance_state import (
    MAINTENANCE_INTERVAL,
    maintenance_schedule,
    record_deferral,
    record_success,
)
from app.corpora import LocalProfile


def test_last_success_persists_date_version_and_result(settings) -> None:
    completed = datetime(2026, 7, 1, 9, tzinfo=UTC)
    state = record_success(
        settings,
        LocalProfile.ADMIN,
        corpus_version=f"corpus-v1-{'a' * 64}",
        job_id=uuid4(),
        completed_at=completed,
    )
    schedule = maintenance_schedule(
        settings,
        LocalProfile.ADMIN,
        now=completed + timedelta(days=1),
    )

    assert schedule.last_success == state
    assert state.result == "published"
    assert schedule.due is False


def test_weekly_due_date_requires_seven_complete_days(settings) -> None:
    completed = datetime(2026, 7, 1, 9, tzinfo=UTC)
    record_success(
        settings,
        LocalProfile.ADMIN,
        corpus_version=f"corpus-v1-{'b' * 64}",
        job_id=uuid4(),
        completed_at=completed,
    )

    before = maintenance_schedule(
        settings,
        LocalProfile.ADMIN,
        now=completed + MAINTENANCE_INTERVAL - timedelta(microseconds=1),
    )
    due = maintenance_schedule(
        settings,
        LocalProfile.ADMIN,
        now=completed + MAINTENANCE_INTERVAL,
    )

    assert before.due is False
    assert due.due is True


def test_deferral_hides_current_prompt_without_moving_real_due_date(settings) -> None:
    now = datetime(2026, 7, 22, 9, tzinfo=UTC)
    record_deferral(settings, LocalProfile.ADMIN, deferred_at=now)

    current_launch = maintenance_schedule(
        settings,
        LocalProfile.ADMIN,
        now=now,
        deferred_for_launch=True,
    )
    next_launch = maintenance_schedule(
        settings,
        LocalProfile.ADMIN,
        now=now + timedelta(minutes=1),
        deferred_for_launch=False,
    )

    assert current_launch.due is True
    assert current_launch.prompt is False
    assert next_launch.due is True
    assert next_launch.prompt is True
    assert next_launch.last_deferred_at == now


def test_user_profile_cannot_read_administrator_schedule(settings) -> None:
    with pytest.raises(PermissionError):
        maintenance_schedule(settings, LocalProfile.USER)
