from __future__ import annotations

import pytest

from app.llm.argo_client import ArgoHealth
from app.llm.argo_key import (
    ARGO_SECRET_RELATIVE_PATH,
    MAX_ARGO_KEY_CHARACTERS,
    ArgoKeyStore,
    argo_connection_status,
    validate_argo_key,
)


def test_argo_key_path_is_local_and_outside_exports(settings) -> None:
    store = ArgoKeyStore(settings)

    assert store.path == (settings.paths.data_dir / ARGO_SECRET_RELATIVE_PATH).resolve()
    assert not store.path.is_relative_to(settings.paths.exports_dir)


def test_argo_key_store_rejects_an_export_tree_secret_path(settings) -> None:
    settings.paths.exports_dir = settings.paths.data_dir

    try:
        ArgoKeyStore(settings)
    except ValueError as exc:
        assert "outside exports" in str(exc)
    else:
        raise AssertionError("secret path inside exports must be rejected")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("   ", "empty"),
        ("x" * (MAX_ARGO_KEY_CHARACTERS + 1), "too long"),
        ("token with-space", "internal whitespace"),
        ("token\nnewline", "internal whitespace"),
    ],
)
def test_argo_key_validation_rejects_malformed_values(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_argo_key(value)


def test_argo_key_validation_trims_edge_whitespace() -> None:
    assert validate_argo_key("  valid-token  ") == "valid-token"


def test_argo_key_public_status_never_contains_key_material(settings) -> None:
    store = ArgoKeyStore(settings)

    payload = store.status().model_dump()

    assert payload == {"configured": False}
    assert set(payload) == {"configured"}


def test_dpapi_ciphertext_is_isolated_between_simulated_windows_accounts(
    settings, monkeypatch
) -> None:
    active_account = ["account-a"]

    def protect(value: bytes, *, description: str) -> bytes:
        del description
        return active_account[0].encode("utf-8") + b":" + value

    def unprotect(value: bytes) -> bytes:
        account, secret = value.split(b":", 1)
        if account.decode("utf-8") != active_account[0]:
            raise OSError("DPAPI account mismatch")
        return secret

    monkeypatch.setattr("app.secrets._protect_windows_data", protect)
    monkeypatch.setattr("app.secrets._unprotect_windows_data", unprotect)
    store = ArgoKeyStore(settings)
    store.save("account-a-secret")

    active_account[0] = "account-b"

    with pytest.raises(OSError, match="account mismatch"):
        store.load()


@pytest.mark.parametrize(
    ("key_configured", "reachable", "model_available", "error", "state", "message_part"),
    [
        (False, False, False, None, "missing", "paramètres"),
        (True, False, False, "ARGO rejected the configured API key", "rejected", "refusée"),
        (True, False, False, "ARGO service is unavailable", "network_unavailable", "VPN"),
        (True, True, False, None, "model_unavailable", "modèle"),
    ],
)
def test_argo_connection_status_is_actionable(
    key_configured: bool,
    reachable: bool,
    model_available: bool,
    error: str | None,
    state: str,
    message_part: str,
) -> None:
    health = ArgoHealth(
        reachable=reachable,
        base_url="https://chatbot.argo.inrae.fr/api",
        configured_model="chat-gpt-oss-20b",
        model_available=model_available,
        available_models=[],
        api_key_configured=key_configured,
        error=error,
    )

    result = argo_connection_status(
        key_configured=key_configured,
        health=health if key_configured else None,
    )

    assert result.state == state
    assert message_part in result.message
