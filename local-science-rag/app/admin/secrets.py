"""Administrator-only DPAPI vault for dedicated bibliographic API keys."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.corpora import LocalProfile
from app.secrets import DpapiFileSecretStore

BibliographicProvider = Literal["openalex", "elsevier", "clarivate", "istex"]
PROVIDER_FILENAME = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")


class AdministratorProfileRequired(PermissionError):
    """An administrator secret operation was attempted from a user profile."""


class AdminBibliographicKeyVault:
    def __init__(self, settings: Settings, profile: LocalProfile) -> None:
        self.settings = settings
        self.profile = profile
        self.root = (settings.paths.data_dir / "admin-secrets" / "bibliographic").resolve()

    def _environment_names(self) -> dict[BibliographicProvider, str]:
        config = self.settings.bibliographic
        return {
            "openalex": config.openalex_api_key_env,
            "elsevier": config.elsevier_api_key_env,
            "clarivate": config.clarivate_api_key_env,
            "istex": self.settings.full_text.istex_token_env,
        }

    def _require_admin(self) -> None:
        if self.profile is not LocalProfile.ADMIN:
            raise AdministratorProfileRequired(
                "Les clés bibliographiques sont réservées au profil administrateur local."
            )

    def _store(self, provider: BibliographicProvider) -> DpapiFileSecretStore:
        if PROVIDER_FILENAME.fullmatch(provider) is None:
            raise ValueError("bibliographic provider is invalid")
        return DpapiFileSecretStore(
            self.root / f"{provider}.dpapi",
            description=f"CiderScholar administrator {provider} key",
        )

    def save(self, provider: BibliographicProvider, secret: str) -> None:
        self._require_admin()
        cleaned = secret.strip()
        if not cleaned or len(cleaned) > 4096 or any(char.isspace() for char in cleaned):
            raise ValueError("La clé bibliographique est vide ou invalide.")
        self._store(provider).save(cleaned)

    def delete(self, provider: BibliographicProvider) -> None:
        self._require_admin()
        self._store(provider).delete()
        os.environ.pop(self._environment_names()[provider], None)

    def status(self) -> dict[BibliographicProvider, bool]:
        self._require_admin()
        return {
            provider: self._store(provider).configured() for provider in self._environment_names()
        }

    def hydrate_process_environment(self) -> None:
        """Expose decrypted values only to this administrator process."""

        names = self._environment_names()
        for environment_name in names.values():
            os.environ.pop(environment_name, None)
        if self.profile is not LocalProfile.ADMIN:
            return
        for provider, environment_name in names.items():
            value = self._store(provider).load()
            if value:
                os.environ[environment_name] = value

    def paths(self) -> list[Path]:
        """Return vault locations for exclusion tests, never their content."""

        self._require_admin()
        return [self._store(provider).path for provider in self._environment_names()]
