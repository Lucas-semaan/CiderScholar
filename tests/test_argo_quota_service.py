from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta

from app.database.sqlite import Database
from app.llm.argo_quota import ArgoQuotaPolicy, QuotaWindow
from app.services.argo_quota import ArgoQuotaService


def test_argo_quota_reservation_is_atomic_between_local_workers(settings) -> None:
    policy = ArgoQuotaPolicy((QuotaWindow(1, timedelta(minutes=1)),))
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    Database(settings.paths.database_path).initialize()

    def reserve() -> bool:
        service = ArgoQuotaService(
            Database(settings.paths.database_path),
            policy=policy,
            windows_user="test-user",
        )
        return service.reserve("chat/completions", now=now).allowed

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: reserve(), range(2)))

    assert sorted(results) == [False, True]


def test_argo_quota_reservation_records_endpoint_without_content(settings) -> None:
    database = Database(settings.paths.database_path)
    service = ArgoQuotaService(database, windows_user="test-user")
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)

    reservation = service.reserve("models", now=now)

    assert reservation.allowed
    with closing(database.connect()) as connection:
        row = connection.execute(
            "SELECT windows_user, endpoint, requested_at FROM argo_request_events"
        ).fetchone()
    assert tuple(row) == ("test-user", "models", now.isoformat())
