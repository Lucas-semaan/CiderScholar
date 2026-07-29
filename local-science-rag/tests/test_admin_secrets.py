from __future__ import annotations

import pytest

import app.secrets as secret_module
from app.admin.secrets import (
    AdminBibliographicKeyVault,
    AdministratorProfileRequired,
)
from app.corpora import LocalProfile
from app.corpus_packages.layout import common_package_files


@pytest.fixture
def fake_dpapi(monkeypatch):
    monkeypatch.setattr(
        secret_module,
        "_protect_windows_data",
        lambda value, *, description: b"protected:" + description.encode() + b":" + value,
    )
    monkeypatch.setattr(
        secret_module,
        "_unprotect_windows_data",
        lambda value: value.rsplit(b":", 1)[1],
    )


def test_user_profile_cannot_configure_administrator_bibliographic_key(
    settings,
    fake_dpapi,
) -> None:
    vault = AdminBibliographicKeyVault(settings, LocalProfile.USER)

    with pytest.raises(AdministratorProfileRequired):
        vault.save("openalex", "private-key")


def test_admin_vault_is_dpapi_only_and_hydrates_only_admin_process(
    settings,
    fake_dpapi,
    monkeypatch,
) -> None:
    variable = settings.bibliographic.openalex_api_key_env
    admin = AdminBibliographicKeyVault(settings, LocalProfile.ADMIN)
    admin.save("openalex", "private-key")
    ciphertext = admin.paths()[0].read_text(encoding="ascii")

    assert "private-key" not in ciphertext
    monkeypatch.setenv(variable, "raw-environment-key")
    AdminBibliographicKeyVault(settings, LocalProfile.USER).hydrate_process_environment()
    assert variable not in secret_module.os.environ

    admin.hydrate_process_environment()
    assert secret_module.os.environ[variable] == "private-key"


def test_administrator_vault_is_outside_common_package_allowlist(
    settings,
    fake_dpapi,
) -> None:
    vault = AdminBibliographicKeyVault(settings, LocalProfile.ADMIN)
    vault.save("clarivate", "private-key")
    package_paths = common_package_files(settings)

    assert all("admin-secrets" not in path.parts for path in package_paths)
    assert vault.paths()[2] not in package_paths


def test_administrator_vault_hydrates_istex_token(
    settings,
    fake_dpapi,
    monkeypatch,
) -> None:
    variable = settings.full_text.istex_token_env
    vault = AdminBibliographicKeyVault(settings, LocalProfile.ADMIN)
    vault.save("istex", "istex-token")
    monkeypatch.delenv(variable, raising=False)

    vault.hydrate_process_environment()

    assert secret_module.os.environ[variable] == "istex-token"
    assert "istex-token" not in vault.paths()[3].read_text(encoding="ascii")
