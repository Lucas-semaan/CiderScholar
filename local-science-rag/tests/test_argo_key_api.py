from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from app.api import argo_key as argo_key_api
from app.llm.argo_client import ArgoHealth
from app.llm.argo_key import ArgoKeyStore
from app.main import create_app


def test_argo_key_endpoint_saves_and_replaces_without_echoing_key(settings, monkeypatch) -> None:
    saved: list[str] = []
    invalidations: list[bool] = []
    monkeypatch.setattr(ArgoKeyStore, "save", lambda _self, key: saved.append(key))
    monkeypatch.setattr(
        argo_key_api,
        "clear_model_validation_cache",
        lambda: invalidations.append(True),
    )

    with TestClient(create_app(settings)) as client:
        first = client.put("/api/argo-key", json={"key": "first-token"})
        replacement = client.put("/api/argo-key", json={"key": "replacement-token"})

    assert first.status_code == 200
    assert replacement.status_code == 200
    assert replacement.json() == {"configured": True}
    assert "replacement-token" not in replacement.text
    assert saved == ["first-token", "replacement-token"]
    assert invalidations == [True, True]


def test_argo_key_endpoint_rejects_unknown_fields(settings, monkeypatch) -> None:
    saved: list[str] = []
    monkeypatch.setattr(ArgoKeyStore, "save", lambda _self, key: saved.append(key))

    with TestClient(create_app(settings)) as client:
        response = client.put(
            "/api/argo-key",
            json={"key": "token", "unexpected": "field"},
        )

    assert response.status_code == 422
    assert saved == []


def test_argo_key_delete_removes_configuration_without_echo(settings, monkeypatch) -> None:
    deleted: list[bool] = []
    invalidations: list[bool] = []
    monkeypatch.setattr(ArgoKeyStore, "delete", lambda _self: deleted.append(True))
    monkeypatch.setattr(
        argo_key_api,
        "clear_model_validation_cache",
        lambda: invalidations.append(True),
    )

    with TestClient(create_app(settings)) as client:
        response = client.delete("/api/argo-key")

    assert response.status_code == 200
    assert response.json() == {"configured": False}
    assert deleted == [True]
    assert invalidations == [True]


def test_argo_key_connection_test_uses_models_without_generation(settings, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(ArgoKeyStore, "load", lambda _self: "stored-token")

    class FakeClient:
        def __init__(self, _settings, *, api_key):
            assert api_key == "stored-token"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def health(self) -> ArgoHealth:
            calls.append("models")
            return ArgoHealth(
                reachable=True,
                base_url="https://chatbot.argo.inrae.fr/api",
                api_key_configured=True,
                configured_model="chat-gpt-oss-20b",
                model_available=True,
                available_models=["chat-gpt-oss-20b"],
            )

        def chat(self, *_args, **_kwargs):
            raise AssertionError("connection test must not generate text")

    monkeypatch.setattr(argo_key_api, "ArgoClient", FakeClient)

    with TestClient(create_app(settings)) as client:
        response = client.post("/api/argo-key/test")

    assert response.status_code == 200
    assert response.json()["state"] == "ready"
    assert calls == ["models"]


def test_argo_key_sentinel_is_absent_from_sqlite_logs_and_responses(
    settings,
    monkeypatch,
    caplog,
) -> None:
    sentinel = "CIDERSCHOLAR-ARGO-SENTINEL-7f62a93d"

    def protect(value: bytes, *, description: str) -> bytes:
        assert description == "CiderScholar ARGO API key"
        return bytes(byte ^ 0xA5 for byte in value)

    def unprotect(value: bytes) -> bytes:
        return bytes(byte ^ 0xA5 for byte in value)

    class FakeClient:
        def __init__(self, _settings, *, api_key):
            assert api_key == sentinel

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def health(self) -> ArgoHealth:
            return ArgoHealth(
                reachable=True,
                base_url="https://chatbot.argo.inrae.fr/api",
                api_key_configured=True,
                configured_model="chat-gpt-oss-20b",
                model_available=True,
                available_models=["chat-gpt-oss-20b"],
            )

    monkeypatch.setattr("app.secrets._protect_windows_data", protect)
    monkeypatch.setattr("app.secrets._unprotect_windows_data", unprotect)
    monkeypatch.setattr(argo_key_api, "ArgoClient", FakeClient)
    caplog.set_level(logging.DEBUG)

    with TestClient(create_app(settings)) as client:
        responses = [
            client.put("/api/argo-key", json={"key": sentinel}),
            client.get("/api/argo-key"),
            client.post("/api/argo-key/test"),
        ]

    assert all(response.status_code == 200 for response in responses)
    sentinel_bytes = sentinel.encode("utf-8")
    assert sentinel_bytes not in settings.paths.database_path.read_bytes()
    assert sentinel not in caplog.text
    assert sentinel not in "\n".join(response.text for response in responses)
