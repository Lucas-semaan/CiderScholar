from fastapi.testclient import TestClient

from app.llm.argo_client import ArgoHealth
from app.main import create_app


def test_health_exposes_local_bind_with_argo_enabled(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "offline_mode": False,
        "network_bind": "127.0.0.1",
        "database": "configured",
    }


def test_argo_health_endpoint_checks_access_without_generation(settings, monkeypatch) -> None:
    health = ArgoHealth(
        reachable=True,
        base_url="https://chatbot.argo.inrae.fr/api",
        configured_model="chat-gpt-oss-20b",
        model_available=True,
        available_models=["chat-gpt-oss-20b"],
        api_key_configured=True,
    )

    class FakeArgoClient:
        def __init__(self, _settings) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def health(self) -> ArgoHealth:
            return health

    monkeypatch.setattr("app.api.health.ArgoClient", FakeArgoClient)
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/llm")

    assert response.status_code == 200
    assert response.json()["provider"] == "argo"
    assert response.json()["model_available"] is True
