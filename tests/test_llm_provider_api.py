from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from app.api import llm_providers as llm_providers_api
from app.llm.argo_client import ArgoClient, ArgoHealth
from app.llm.providers import LlmProviderStore
from app.main import create_app


def _use_test_dpapi(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.secrets._protect_windows_data",
        lambda value, *, description: bytes(byte ^ 0xA5 for byte in value),
    )
    monkeypatch.setattr(
        "app.secrets._unprotect_windows_data",
        lambda value: bytes(byte ^ 0xA5 for byte in value),
    )


def test_llm_provider_api_configures_custom_profile_without_exposing_key(
    settings, monkeypatch
) -> None:
    settings.app.generation_provider_policy = "provider_selectable"
    _use_test_dpapi(monkeypatch)
    sentinel = "CIDERSCHOLAR-CUSTOM-LLM-SENTINEL-7f62a93d"

    with TestClient(create_app(settings)) as client:
        initial = client.get("/api/llm-providers")
        configured = client.put(
            "/api/llm-providers/custom",
            json={
                "key": sentinel,
                "base_url": "https://custom.example/v1",
                "model": "custom-model",
            },
        )
        listed = client.get("/api/llm-providers")

    assert initial.status_code == 200
    assert initial.json()["active_provider"] == "argo"
    assert configured.status_code == 200
    assert configured.json() == {
        "id": "custom",
        "label": "Fournisseur personnalisé",
        "base_url": "https://custom.example/v1",
        "model": "custom-model",
        "key_configured": True,
        "active": False,
        "endpoint_editable": True,
    }
    assert listed.status_code == 200
    assert sentinel not in configured.text
    assert sentinel not in listed.text


def test_llm_provider_api_rejects_unsafe_custom_endpoint_and_unknown_fields(
    settings, monkeypatch
) -> None:
    settings.app.generation_provider_policy = "provider_selectable"
    _use_test_dpapi(monkeypatch)

    with TestClient(create_app(settings)) as client:
        unsafe = client.put(
            "/api/llm-providers/custom",
            json={
                "key": "custom-token",
                "base_url": "http://custom.example/v1",
                "model": "custom-model",
            },
        )
        unknown = client.put(
            "/api/llm-providers/custom",
            json={
                "key": "custom-token",
                "base_url": "https://custom.example/v1",
                "model": "custom-model",
                "unexpected": "field",
            },
        )

    assert unsafe.status_code == 422
    assert unknown.status_code == 422


def test_llm_provider_api_tests_then_activates_selected_provider(settings, monkeypatch) -> None:
    settings.app.generation_provider_policy = "provider_selectable"
    _use_test_dpapi(monkeypatch)
    calls: list[str] = []

    class FakeClient:
        def __init__(self, _settings, *, provider_id: str) -> None:
            calls.append(provider_id)

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def health(self) -> ArgoHealth:
            return ArgoHealth(
                reachable=True,
                provider="custom",
                base_url="https://custom.example/v1",
                configured_model="custom-model",
                model_available=True,
                available_models=["custom-model"],
                api_key_configured=True,
            )

    monkeypatch.setattr(llm_providers_api, "ArgoClient", FakeClient)

    with TestClient(create_app(settings)) as client:
        configured = client.put(
            "/api/llm-providers/custom",
            json={
                "key": "custom-token",
                "base_url": "https://custom.example/v1",
                "model": "custom-model",
            },
        )
        tested = client.post("/api/llm-providers/custom/test")
        activated = client.put("/api/llm-providers/active", json={"provider": "custom"})
        listed = client.get("/api/llm-providers")

    assert configured.status_code == 200
    assert tested.status_code == 200
    assert tested.json()["state"] == "ready"
    assert activated.status_code == 200
    assert activated.json()["active"] is True
    assert listed.json()["active_provider"] == "custom"
    assert calls == ["custom", "custom"]


def test_active_custom_provider_uses_openai_compatible_models_and_chat(
    settings, monkeypatch
) -> None:
    settings.app.generation_provider_policy = "provider_selectable"
    _use_test_dpapi(monkeypatch)
    requests: list[httpx.Request] = []
    store = LlmProviderStore(settings)
    store.configure(
        "custom",
        key="custom-token",
        base_url="https://custom.example/v1",
        model="custom-model",
    )
    store.activate("custom")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "custom-model"}]})
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer custom-token"
        assert json.loads(request.content) == {
            "model": "custom-model",
            "messages": [{"role": "user", "content": "Bonjour"}],
            "stream": False,
            "temperature": settings.argo.temperature,
            "max_tokens": settings.argo.max_output_tokens,
        }
        return httpx.Response(
            200,
            json={
                "model": "custom-model",
                "choices": [{"message": {"role": "assistant", "content": "Salut"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    with ArgoClient(settings, transport=httpx.MockTransport(handler)) as client:
        health = client.health()
        response = client.chat([{"role": "user", "content": "Bonjour"}])

    assert health.provider == "custom"
    assert health.base_url == "https://custom.example/v1"
    assert health.model_available is True
    assert response.content == "Salut"
    assert [request.url.path for request in requests] == [
        "/v1/models",
        "/v1/models",
        "/v1/chat/completions",
    ]


def test_llm_provider_api_delete_custom_clears_profile_and_reselects_argo(
    settings, monkeypatch
) -> None:
    settings.app.generation_provider_policy = "provider_selectable"
    _use_test_dpapi(monkeypatch)

    with TestClient(create_app(settings)) as client:
        client.put(
            "/api/llm-providers/custom",
            json={
                "key": "custom-token",
                "base_url": "https://custom.example/v1",
                "model": "custom-model",
            },
        )
        # The active choice is persisted independently from credentials.
        LlmProviderStore(settings)._save_state(
            LlmProviderStore(settings).load_state().model_copy(update={"active_provider": "custom"})
        )
        deleted = client.delete("/api/llm-providers/custom")
        listed = client.get("/api/llm-providers")

    assert deleted.status_code == 200
    assert deleted.json()["key_configured"] is False
    assert deleted.json()["base_url"] == ""
    assert deleted.json()["model"] == ""
    assert listed.json()["active_provider"] == "argo"
