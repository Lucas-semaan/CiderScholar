from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime

import httpx
import pytest

import app.llm.argo_client as argo_client_module
from app.database.sqlite import Database
from app.llm.argo_client import (
    ArgoAuthenticationError,
    ArgoAuthorizationError,
    ArgoClient,
    ArgoGenerationError,
    ArgoQuotaError,
    clear_model_validation_cache,
)
from app.services.argo_quota import ArgoQuotaReservation


def _models() -> dict[str, object]:
    return {
        "data": [
            {"id": "assistant-inrae"},
            {"id": "chat-gpt-oss-120b"},
        ]
    }


def test_argo_health_uses_bearer_auth_without_generation(settings) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url == "https://chatbot.argo.inrae.fr/api/models"
        return httpx.Response(200, json=_models())

    with ArgoClient(
        settings,
        api_key="unit-test-secret",
        transport=httpx.MockTransport(handler),
    ) as client:
        health = client.health()

    assert health.reachable is True
    assert health.model_available is True
    assert health.api_key_configured is True
    assert health.available_models == ["assistant-inrae", "chat-gpt-oss-120b"]
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer unit-test-secret"


def test_argo_client_does_not_keep_an_accessible_key_copy(settings) -> None:
    secret = "unit-test-secret"
    client = ArgoClient(
        settings, api_key=secret, transport=httpx.MockTransport(lambda _request: None)
    )

    try:
        assert not hasattr(client, "_api_key")
        public_state = {
            name: value for name, value in vars(client).items() if not name.startswith("_")
        }
        assert secret not in repr(public_state)
    finally:
        client.close()


def test_argo_client_uses_personal_dpapi_key_before_environment(settings, monkeypatch) -> None:
    monkeypatch.setenv(settings.argo.api_key_env, "environment-fallback")
    monkeypatch.setattr(
        "app.llm.argo_key.ArgoKeyStore.load",
        lambda _store: "personal-dpapi-key",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer personal-dpapi-key"
        return httpx.Response(200, json=_models())

    with ArgoClient(settings, transport=httpx.MockTransport(handler)) as client:
        assert client.list_models() == ["assistant-inrae", "chat-gpt-oss-120b"]


def test_argo_client_uses_environment_when_personal_key_is_absent(settings, monkeypatch) -> None:
    monkeypatch.setenv(settings.argo.api_key_env, "environment-fallback")
    monkeypatch.setattr("app.llm.argo_key.ArgoKeyStore.load", lambda _store: None)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer environment-fallback"
        return httpx.Response(200, json=_models())

    with ArgoClient(settings, transport=httpx.MockTransport(handler)) as client:
        assert client.list_models() == ["assistant-inrae", "chat-gpt-oss-120b"]


def test_model_validation_cache_is_shared_and_time_bounded(settings, monkeypatch) -> None:
    calls = {"models": 0, "chat": 0}
    current_time = [100.0]
    monkeypatch.setattr(argo_client_module, "monotonic", lambda: current_time[0])
    settings.argo.model_validation_ttl_seconds = 30
    clear_model_validation_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            calls["models"] += 1
            return httpx.Response(200, json=_models())
        calls["chat"] += 1
        return httpx.Response(
            200,
            json={
                "model": "chat-gpt-oss-120b",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    try:
        for _ in range(2):
            with ArgoClient(
                settings,
                api_key="cache-test-key",
                transport=httpx.MockTransport(handler),
            ) as client:
                client.chat([{"role": "user", "content": "Question"}])
        assert calls == {"models": 1, "chat": 2}

        current_time[0] += 31
        with ArgoClient(
            settings,
            api_key="cache-test-key",
            transport=httpx.MockTransport(handler),
        ) as client:
            client.chat([{"role": "user", "content": "Question"}])
        assert calls == {"models": 2, "chat": 3}
    finally:
        clear_model_validation_cache()


def test_argo_chat_sends_schema_and_maps_usage(settings) -> None:
    request_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/models":
            return httpx.Response(200, json=_models())
        assert request.url.path == "/api/chat/completions"
        request_bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "chat-gpt-oss-120b",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"answer":4}',
                            "reasoning_content": "not persisted",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 25, "completion_tokens": 12},
            },
        )

    with ArgoClient(
        settings,
        api_key="unit-test-secret",
        transport=httpx.MockTransport(handler),
    ) as client:
        response = client.chat(
            [{"role": "user", "content": "What is 2 + 2?"}],
            json_schema={
                "type": "object",
                "properties": {"answer": {"type": "integer"}},
                "required": ["answer"],
            },
            max_output_tokens=256,
        )

    assert response.content == '{"answer":4}'
    assert response.done_reason == "stop"
    assert response.metrics.prompt_eval_count == 25
    assert response.metrics.eval_count == 12
    body = request_bodies[0]
    assert body["model"] == "chat-gpt-oss-120b"
    assert body["stream"] is False
    assert body["max_tokens"] == 256
    assert body["response_format"]["type"] == "json_schema"  # type: ignore[index]
    assert "reasoning_content" not in response.model_dump_json()


def test_argo_missing_or_rejected_key_is_reported_without_leaking(settings) -> None:
    client = ArgoClient(settings, api_key="")
    try:
        health = client.health()
    finally:
        client.close()
    assert health.reachable is False
    assert health.api_key_configured is False
    assert settings.argo.api_key_env in str(health.error)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid token"})

    with (
        ArgoClient(
            settings,
            api_key="secret-that-must-not-leak",
            transport=httpx.MockTransport(handler),
        ) as rejected,
        pytest.raises(ArgoAuthenticationError) as exc_info,
    ):
        rejected.list_models()
    assert "secret-that-must-not-leak" not in str(exc_info.value)


def test_argo_forbidden_operation_does_not_blame_the_key(settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "model access denied"})

    with ArgoClient(
        settings,
        api_key="valid-but-not-authorized",
        transport=httpx.MockTransport(handler),
    ) as client:
        health = client.health()
        with pytest.raises(ArgoAuthorizationError, match="model or operation"):
            client.list_models()

    assert health.reachable is True
    assert health.model_available is False
    assert health.api_key_configured is True


def test_argo_quota_error_is_explicit(settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "quota"})

    with (
        ArgoClient(
            settings,
            api_key="unit-test-secret",
            transport=httpx.MockTransport(handler),
        ) as client,
        pytest.raises(ArgoQuotaError, match="quota"),
    ):
        client.list_models()


def test_local_quota_counts_models_retries_and_http_errors(settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "temporary failure"})

    with ArgoClient(
        settings,
        api_key="conservative-counting-key",
        transport=httpx.MockTransport(handler),
    ) as client:
        for _attempt in range(2):
            with pytest.raises(ArgoGenerationError):
                client.list_models()

    database = Database(settings.paths.database_path)
    with closing(database.connect()) as connection:
        rows = connection.execute(
            """
            SELECT endpoint
            FROM argo_request_events
            WHERE endpoint = 'models'
            ORDER BY id
            """
        ).fetchall()

    assert [row[0] for row in rows] == ["models", "models"]


def test_chat_hook_runs_after_quota_reservation_and_before_http(settings) -> None:
    order: list[str] = []

    class RecordingQuota:
        def reserve(self, endpoint: str) -> ArgoQuotaReservation:
            order.append(f"quota:{endpoint}")
            return ArgoQuotaReservation(True, datetime.now(UTC))

    def handler(request: httpx.Request) -> httpx.Response:
        order.append(f"http:{request.url.path.rsplit('/', 1)[-1]}")
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=_models())
        return httpx.Response(
            200,
            json={
                "model": "chat-gpt-oss-120b",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    clear_model_validation_cache()
    try:
        with ArgoClient(
            settings,
            api_key="reservation-order-key",
            transport=httpx.MockTransport(handler),
            quota_service=RecordingQuota(),
        ) as client:
            client.chat(
                [{"role": "user", "content": "Question"}],
                on_request_reserved=lambda: order.append("event:argo"),
            )
    finally:
        clear_model_validation_cache()

    assert order[-3:] == ["quota:chat/completions", "event:argo", "http:completions"]
