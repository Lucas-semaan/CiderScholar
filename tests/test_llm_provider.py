from __future__ import annotations

import pytest

from app.llm.argo_key import ArgoKeyStore
from app.llm.providers import (
    ARGO_BASE_URL,
    CustomLlmKeyStore,
    LlmProviderStore,
    migrate_legacy_argo_key,
    validate_custom_endpoint,
)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://provider.example/v1",
        "https://user:password@provider.example/v1",
        "https://provider.example/v1?tenant=local",
        "https://provider.example/v1#models",
        "provider.example/v1",
    ],
)
def test_custom_endpoint_rejects_non_safe_https_urls(endpoint: str) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        validate_custom_endpoint(endpoint)


def test_custom_endpoint_normalizes_safe_https_url() -> None:
    assert (
        validate_custom_endpoint(" https://provider.example/v1/ ") == "https://provider.example/v1"
    )


def test_provider_state_and_active_selection_persist(settings, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.secrets._protect_windows_data",
        lambda value, *, description: b"protected:" + description.encode() + b":" + value,
    )
    monkeypatch.setattr(
        "app.secrets._unprotect_windows_data",
        lambda value: value.rsplit(b":", maxsplit=1)[1],
    )
    store = LlmProviderStore(settings)

    configured = store.configure(
        "custom",
        key="custom-token",
        base_url="https://custom.example/v1",
        model="custom-model",
    )
    active = store.activate("custom")
    reloaded = LlmProviderStore(settings)

    assert configured.active is False
    assert active.active is True
    assert reloaded.active_provider() == "custom"
    assert reloaded.active_profile().model == "custom-model"
    assert reloaded.active_profile().base_url == "https://custom.example/v1"
    assert reloaded.active_profile().key_configured is True
    assert "custom-token" not in reloaded.path.read_text(encoding="utf-8")


def test_argo_and_custom_dpapi_keys_are_isolated(settings, monkeypatch) -> None:
    protected_descriptions: list[str] = []

    def protect(value: bytes, *, description: str) -> bytes:
        protected_descriptions.append(description)
        return bytes(byte ^ 0xA5 for byte in value)

    monkeypatch.setattr("app.secrets._protect_windows_data", protect)
    monkeypatch.setattr(
        "app.secrets._unprotect_windows_data",
        lambda value: bytes(byte ^ 0xA5 for byte in value),
    )
    store = LlmProviderStore(settings)
    store.configure("argo", key="argo-token")
    store.configure(
        "custom",
        key="custom-token",
        base_url="https://custom.example/v1",
        model="custom-model",
    )

    argo_store = ArgoKeyStore(settings)
    custom_store = CustomLlmKeyStore(settings)

    assert argo_store.path != custom_store.path
    assert argo_store.load() == "argo-token"
    assert custom_store.load() == "custom-token"
    assert protected_descriptions == [
        "CiderScholar ARGO API key",
        "CiderScholar custom LLM API key",
    ]


def test_legacy_argo_key_remains_the_canonical_provider_key(settings, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.secrets._protect_windows_data",
        lambda value, *, description: value,
    )
    monkeypatch.setattr("app.secrets._unprotect_windows_data", lambda value: value)
    legacy_store = ArgoKeyStore(settings)
    legacy_store.save("legacy-argo-token")

    provider_store = LlmProviderStore(settings)

    assert migrate_legacy_argo_key(settings) is True
    assert provider_store.profile("argo").base_url == ARGO_BASE_URL
    assert provider_store.api_key("argo") == "legacy-argo-token"
    assert provider_store.profile("argo").key_configured is True


def test_custom_provider_must_be_complete_before_activation(settings) -> None:
    store = LlmProviderStore(settings)

    with pytest.raises(ValueError, match="fully configured"):
        store.activate("custom")
