from __future__ import annotations

from fastapi.testclient import TestClient

from app.corpora import LOCAL_PROFILE_ENV
from app.main import create_app


def test_user_profile_cannot_activate_or_view_administrator_maintenance(settings) -> None:
    with TestClient(create_app(settings)) as client:
        status = client.get("/api/admin/maintenance")
        launch = client.post(
            "/api/admin/maintenance/launch",
            json={"confirmed": True},
        )

    assert status.status_code == 403
    assert launch.status_code == 403


def test_admin_can_launch_or_defer_but_deferral_returns_next_launch(
    settings,
    monkeypatch,
) -> None:
    monkeypatch.setenv(LOCAL_PROFILE_ENV, "admin")
    with TestClient(create_app(settings)) as client:
        initial = client.get("/api/admin/maintenance")
        invalid = client.post(
            "/api/admin/maintenance/defer",
            json={"confirmed": False},
        )
        deferred = client.post(
            "/api/admin/maintenance/defer",
            json={"confirmed": True},
        )

    assert initial.json()["prompt"] is True
    assert invalid.status_code == 422
    assert deferred.json()["due"] is True
    assert deferred.json()["prompt"] is False

    with TestClient(create_app(settings)) as next_launch:
        returned = next_launch.get("/api/admin/maintenance")
        first = next_launch.post(
            "/api/admin/maintenance/launch",
            json={"confirmed": True},
        )
        duplicate = next_launch.post(
            "/api/admin/maintenance/launch",
            json={"confirmed": True},
        )

    assert returned.json()["prompt"] is True
    assert first.status_code == 200
    assert first.json()["type"] == "weekly_maintenance"
    assert duplicate.json()["id"] == first.json()["id"]
