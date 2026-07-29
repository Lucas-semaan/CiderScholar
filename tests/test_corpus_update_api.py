from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.parametrize(
    "path",
    [
        "/api/corpus-updates/download",
        "/api/corpus-updates/install-on-restart",
        "/api/corpus-updates/rollback-on-restart",
    ],
)
def test_corpus_update_actions_require_explicit_confirmation(settings, path: str) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(path, json={"confirmed": False})

    assert response.status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/api/corpus-updates/download",
        "/api/corpus-updates/install-on-restart",
        "/api/corpus-updates/rollback-on-restart",
    ],
)
def test_unavailable_corpus_action_has_a_readable_conflict(settings, path: str) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(path, json={"confirmed": True})

    assert response.status_code == 409
    assert response.json()["detail"]
