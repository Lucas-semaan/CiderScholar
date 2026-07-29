from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.corpus_packages import checks
from app.corpus_packages.updates import LatestReadResult, LatestState, UpdateComparison
from app.main import create_app


def test_corpus_update_check_reads_sync_at_most_once_per_day(settings, monkeypatch) -> None:
    calls = {"comparison": 0, "latest": 0}

    def comparison(_settings):
        calls["comparison"] += 1
        return UpdateComparison(
            latest_state=LatestState.LATEST_UNAVAILABLE,
            installed_version=None,
            available_version=None,
            update_available=False,
            download_required=False,
            message="Aucune mise à jour.",
        )

    def latest(_settings):
        calls["latest"] += 1
        return LatestReadResult(
            state=LatestState.LATEST_UNAVAILABLE,
            message="Aucune mise à jour.",
        )

    monkeypatch.setattr(checks, "compare_corpus_versions", comparison)
    monkeypatch.setattr(checks, "read_latest_manifest", latest)
    first_time = datetime(2026, 7, 22, 8, tzinfo=UTC)

    first = checks.refresh_corpus_update_if_due(settings, now=first_time)
    cached = checks.refresh_corpus_update_if_due(
        settings,
        now=first_time + timedelta(hours=23),
    )
    refreshed = checks.refresh_corpus_update_if_due(
        settings,
        now=first_time + timedelta(hours=24),
    )

    assert cached.checked_at == first.checked_at
    assert refreshed.checked_at != first.checked_at
    assert calls == {"comparison": 2, "latest": 2}


def test_sharepoint_check_failure_does_not_block_local_application(
    settings,
    monkeypatch,
) -> None:
    def unavailable(_settings):
        raise OSError("sync unavailable")

    monkeypatch.setattr(checks, "compare_corpus_versions", unavailable)

    with TestClient(create_app(settings)) as client:
        health = client.get("/health")
        conversations = client.get("/api/chatbot/conversations")
        runtime = client.get("/api/system/settings")

    assert health.status_code == 200
    assert conversations.status_code == 200
    assert runtime.status_code == 200
    assert runtime.json()["corpus_update"]["latest_state"] == "sync_unavailable"
    assert "reste utilisable" in runtime.json()["corpus_update"]["message"]
