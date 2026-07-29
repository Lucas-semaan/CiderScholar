from __future__ import annotations

from contextlib import nullcontext

import pytest

import app.secrets as secrets

pytestmark = pytest.mark.skipif(secrets.winreg is None, reason="Windows-only environment store")


def test_local_secret_store_contract_is_framework_independent() -> None:
    class MemorySecretStore:
        def configured(self) -> bool:
            return False

        def save(self, secret: str) -> None:
            del secret

        def load(self) -> str | None:
            return None

        def delete(self) -> None:
            return None

    assert isinstance(MemorySecretStore(), secrets.LocalSecretStore)


def test_dpapi_file_store_contains_only_versioned_ciphertext(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        secrets,
        "_protect_windows_data",
        lambda value, *, description: b"protected:" + description.encode() + b":" + value,
    )
    monkeypatch.setattr(
        secrets,
        "_unprotect_windows_data",
        lambda value: value.rsplit(b":", 1)[1],
    )
    path = tmp_path / "argo-key.dpapi"
    store = secrets.DpapiFileSecretStore(path, description="CiderScholar ARGO key")

    store.save("first-secret")
    assert store.configured()
    assert path.read_text(encoding="ascii").startswith(secrets.DPAPI_PREFIX)
    assert "first-secret" not in path.read_text(encoding="ascii")
    assert store.load() == "first-secret"

    store.save("replacement-secret")
    assert store.load() == "replacement-secret"
    store.delete()
    assert not store.configured()
    assert store.load() is None


def test_hydrates_a_missing_process_variable_from_windows_user_environment(
    monkeypatch,
) -> None:
    variable_name = "CIDERSCHOLAR_TEST_SECRET"
    monkeypatch.delenv(variable_name, raising=False)
    monkeypatch.setattr(
        secrets.winreg,
        "OpenKey",
        lambda *_args: nullcontext(object()),
    )

    def query_value(_key, name):
        if name != variable_name:
            raise FileNotFoundError
        return "  configured-value  ", 1

    monkeypatch.setattr(secrets.winreg, "QueryValueEx", query_value)

    secrets.hydrate_user_environment([variable_name])

    assert secrets.os.environ[variable_name] == "configured-value"


def test_existing_process_variable_has_priority(monkeypatch) -> None:
    variable_name = "CIDERSCHOLAR_TEST_SECRET"
    monkeypatch.setenv(variable_name, "process-value")

    def unexpected_query(*_args):
        raise AssertionError("registry must not be read")

    monkeypatch.setattr(secrets.winreg, "QueryValueEx", unexpected_query)

    secrets.hydrate_user_environment([variable_name])

    assert secrets.os.environ[variable_name] == "process-value"


def test_protected_secret_is_persisted_only_as_dpapi_ciphertext(monkeypatch) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setattr(secrets, "_protect_windows_data", lambda value: b"protected:" + value)
    monkeypatch.setattr(
        secrets,
        "persist_user_environment_value",
        lambda name, value: captured.update(name=name, value=value),
    )

    secrets.persist_protected_user_secret("CIDERSCHOLAR_TEST_PASSWORD", "plain-secret")

    assert captured["name"] == "CIDERSCHOLAR_TEST_PASSWORD"
    assert captured["value"].startswith(secrets.DPAPI_PREFIX)
    assert "plain-secret" not in captured["value"]
