"""Atomic SQLite reservations for the current Windows user's ARGO quota."""

from __future__ import annotations

import getpass
from dataclasses import dataclass
from datetime import UTC, datetime

from app.database.sqlite import Database
from app.llm.argo_quota import ArgoQuotaPolicy


@dataclass(frozen=True)
class ArgoQuotaReservation:
    allowed: bool
    next_allowed_at: datetime


class ArgoQuotaService:
    def __init__(
        self,
        database: Database,
        *,
        policy: ArgoQuotaPolicy | None = None,
        windows_user: str | None = None,
    ) -> None:
        self.database = database
        self.policy = policy or ArgoQuotaPolicy()
        self.windows_user = (windows_user or getpass.getuser()).strip()
        if not self.windows_user:
            raise ValueError("Windows user identity cannot be empty")
        self.database.initialize()

    def has_capacity(self, request_count: int = 1, *, now: datetime | None = None) -> bool:
        """Check whether a bounded batch fits without consuming a quota slot."""

        if request_count < 1:
            raise ValueError("request count must be positive")
        requested_at = now or datetime.now(UTC)
        if requested_at.tzinfo is None or requested_at.utcoffset() is None:
            raise ValueError("ARGO quota reservation time must be timezone-aware")
        cutoff = requested_at - max(window.duration for window in self.policy.windows)
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT requested_at
                FROM argo_request_events
                WHERE windows_user = ? AND requested_at >= ?
                ORDER BY requested_at
                """,
                (self.windows_user, cutoff.isoformat()),
            ).fetchall()
        request_ages = [requested_at - datetime.fromisoformat(str(row[0])) for row in rows]
        return all(
            sum(age < window.duration for age in request_ages) + request_count <= window.limit
            for window in self.policy.windows
        )

    def reserve(self, endpoint: str, *, now: datetime | None = None) -> ArgoQuotaReservation:
        requested_at = now or datetime.now(UTC)
        if requested_at.tzinfo is None or requested_at.utcoffset() is None:
            raise ValueError("ARGO quota reservation time must be timezone-aware")
        normalized_endpoint = endpoint.strip()
        if not normalized_endpoint or len(normalized_endpoint) > 200:
            raise ValueError("ARGO endpoint is invalid")
        cutoff = requested_at - max(window.duration for window in self.policy.windows)
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM argo_request_events WHERE requested_at < ?",
                (cutoff.isoformat(),),
            )
            rows = connection.execute(
                """
                SELECT requested_at
                FROM argo_request_events
                WHERE windows_user = ? AND requested_at >= ?
                ORDER BY requested_at
                """,
                (self.windows_user, cutoff.isoformat()),
            ).fetchall()
            request_times = [datetime.fromisoformat(str(row[0])) for row in rows]
            next_allowed_at = self.policy.next_allowed_at(request_times, now=requested_at)
            if next_allowed_at > requested_at:
                return ArgoQuotaReservation(False, next_allowed_at)
            connection.execute(
                """
                INSERT INTO argo_request_events(windows_user, endpoint, requested_at)
                VALUES (?, ?, ?)
                """,
                (self.windows_user, normalized_endpoint, requested_at.isoformat()),
            )
        return ArgoQuotaReservation(True, requested_at)
