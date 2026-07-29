from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.llm.argo_quota import ArgoQuotaPolicy


@pytest.mark.parametrize(
    ("request_ages", "allowed"),
    [
        ([timedelta(seconds=30)] * 19, True),
        ([timedelta(seconds=30)] * 20, False),
        ([timedelta(minutes=30)] * 119, True),
        ([timedelta(minutes=30)] * 120, False),
        ([timedelta(hours=2)] * 199, True),
        ([timedelta(hours=2)] * 200, False),
    ],
)
def test_argo_quota_policy_enforces_every_window(
    request_ages: list[timedelta], allowed: bool
) -> None:
    assert ArgoQuotaPolicy().allows(request_ages) is allowed


def test_argo_quota_policy_excludes_events_on_window_boundary() -> None:
    ages = [timedelta(minutes=1)] * 20

    assert ArgoQuotaPolicy().allows(ages)


def test_argo_quota_policy_rejects_future_events() -> None:
    with pytest.raises(ValueError, match="negative"):
        ArgoQuotaPolicy().allows([timedelta(seconds=-1)])


@pytest.mark.parametrize(
    ("count", "age", "expected_delay"),
    [
        (19, timedelta(seconds=30), timedelta(0)),
        (20, timedelta(seconds=30), timedelta(seconds=30)),
        (119, timedelta(minutes=30), timedelta(0)),
        (120, timedelta(minutes=30), timedelta(minutes=30)),
        (199, timedelta(hours=2), timedelta(0)),
        (200, timedelta(hours=2), timedelta(hours=1)),
    ],
)
def test_argo_quota_next_allowed_at_limit_boundaries(
    count: int, age: timedelta, expected_delay: timedelta
) -> None:
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    request_times = [now - age] * count

    assert ArgoQuotaPolicy().next_allowed_at(request_times, now=now) == now + expected_delay


def test_argo_quota_next_allowed_expires_enough_events_after_an_overrun() -> None:
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    request_times = [now - timedelta(seconds=age) for age in range(1, 22)]

    next_allowed = ArgoQuotaPolicy().next_allowed_at(request_times, now=now)

    assert next_allowed == now + timedelta(seconds=40)
