"""Local conservative policy for personal ARGO request quotas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class QuotaWindow:
    limit: int
    duration: timedelta


ARGO_QUOTA_WINDOWS = (
    QuotaWindow(limit=20, duration=timedelta(minutes=1)),
    QuotaWindow(limit=120, duration=timedelta(hours=1)),
    QuotaWindow(limit=200, duration=timedelta(minutes=180)),
)


@dataclass(frozen=True)
class ArgoQuotaPolicy:
    windows: tuple[QuotaWindow, ...] = ARGO_QUOTA_WINDOWS

    def allows(self, request_ages: list[timedelta]) -> bool:
        """Return whether one new request fits every sliding window."""

        if any(age < timedelta(0) for age in request_ages):
            raise ValueError("request ages cannot be negative")
        return all(
            sum(age < window.duration for age in request_ages) < window.limit
            for window in self.windows
        )

    def next_allowed_at(
        self,
        request_times: list[datetime],
        *,
        now: datetime,
    ) -> datetime:
        """Return the earliest instant at which one more request fits every window."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("quota time must be timezone-aware")
        ages = [now - request_time for request_time in request_times]
        if self.allows(ages):
            return now
        required_delays: list[timedelta] = []
        for window in self.windows:
            active_ages = sorted(
                (age for age in ages if age < window.duration),
                reverse=True,
            )
            excess = len(active_ages) - window.limit + 1
            if excess > 0:
                age_that_must_expire = active_ages[excess - 1]
                required_delays.append(window.duration - age_that_must_expire)
        return now + max(required_delays, default=timedelta(0))
