"""Persistent current-user profiles for OpenAI-compatible LLM providers."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator

from app.config import Settings
from app.llm.argo_key import ARGO_SECRET_RELATIVE_PATH, ArgoKeyStore, validate_argo_key
from app.secrets import DpapiFileSecretStore

LlmProviderId = Literal["argo", "custom"]
ARGO_BASE_URL = "https://chatbot.argo.inrae.fr/api"
CUSTOM_SECRET_RELATIVE_PATH = Path("secrets") / "custom-llm-key.dpapi"
PROVIDER_STATE_RELATIVE_PATH = Path("preferences") / "llm-providers.json"
_STATE_LOCK = threading.RLock()


def validate_custom_endpoint(value: str) -> str:
    """Accept a remote HTTPS API root without embedded credentials or URL suffix data."""

    cleaned = value.strip().rstrip("/")
    parsed = urlsplit(cleaned)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Custom LLM endpoint must be an HTTPS URL without credentials, query, or fragment"
        )
    return cleaned


def validate_model_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or any(character.isspace() for character in cleaned):
        raise ValueError("LLM model name must be non-empty and contain no spaces")
    return cleaned


class StoredProviderState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    active_provider: LlmProviderId = "argo"
    custom_base_url: str | None = None
    custom_model: str | None = None

    @field_validator("custom_base_url")
    @classmethod
    def validate_optional_endpoint(cls, value: str | None) -> str | None:
        return validate_custom_endpoint(value) if value is not None else None

    @field_validator("custom_model")
    @classmethod
    def validate_optional_model(cls, value: str | None) -> str | None:
        return validate_model_name(value) if value is not None else None


class LlmProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: LlmProviderId
    label: str
    base_url: str
    model: str
    key_configured: bool
    active: bool
    endpoint_editable: bool


class CustomLlmKeyStore(DpapiFileSecretStore):
    """Separate DPAPI secret for the custom provider."""

    def __init__(self, settings: Settings) -> None:
        path = (settings.paths.data_dir / CUSTOM_SECRET_RELATIVE_PATH).resolve()
        exports_dir = settings.paths.exports_dir.resolve()
        if path == exports_dir or path.is_relative_to(exports_dir):
            raise ValueError("Custom LLM secret path must remain outside exports")
        super().__init__(path, description="CiderScholar custom LLM API key")

    def save(self, secret: str) -> None:
        super().save(validate_argo_key(secret))


class LlmProviderStore:
    """Atomically persist non-secret provider preferences beside DPAPI credentials."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = (settings.paths.data_dir / PROVIDER_STATE_RELATIVE_PATH).resolve()
        exports_dir = settings.paths.exports_dir.resolve()
        if self.path == exports_dir or self.path.is_relative_to(exports_dir):
            raise ValueError("LLM provider preferences must remain outside exports")

    def load_state(self) -> StoredProviderState:
        with _STATE_LOCK:
            if not self.path.exists():
                return StoredProviderState()
            if not self.path.is_file() or self.path.stat().st_size > 64 * 1024:
                raise RuntimeError("LLM provider preferences file is invalid")
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                return StoredProviderState.model_validate(payload)
            except (OSError, ValueError) as exc:
                raise RuntimeError("LLM provider preferences could not be loaded") from exc

    def _save_state(self, state: StoredProviderState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        with _STATE_LOCK:
            try:
                with tempfile.NamedTemporaryFile(
                    "w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temporary_path = Path(handle.name)
                    json.dump(state.model_dump(mode="json"), handle, ensure_ascii=True, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, self.path)
            finally:
                if temporary_path is not None and temporary_path.exists():
                    temporary_path.unlink()

    def key_store(self, provider: LlmProviderId) -> DpapiFileSecretStore:
        return (
            ArgoKeyStore(self.settings) if provider == "argo" else CustomLlmKeyStore(self.settings)
        )

    def api_key(self, provider: LlmProviderId) -> str | None:
        stored = self.key_store(provider).load()
        if stored is not None:
            return stored
        if provider == "argo":
            environment_key = os.environ.get(self.settings.argo.api_key_env, "").strip()
            return environment_key or None
        return None

    def profile(self, provider: LlmProviderId) -> LlmProviderProfile:
        state = self.load_state()
        if provider == "argo":
            return LlmProviderProfile(
                id="argo",
                label="ARGO INRAE",
                base_url=ARGO_BASE_URL,
                model=self.settings.argo.model,
                key_configured=self.api_key("argo") is not None,
                active=state.active_provider == "argo",
                endpoint_editable=False,
            )
        return LlmProviderProfile(
            id="custom",
            label="Fournisseur personnalisé",
            base_url=state.custom_base_url or "",
            model=state.custom_model or "",
            key_configured=self.api_key("custom") is not None,
            active=state.active_provider == "custom",
            endpoint_editable=True,
        )

    def profiles(self) -> list[LlmProviderProfile]:
        return [self.profile("argo"), self.profile("custom")]

    def active_provider(self) -> LlmProviderId:
        return self.load_state().active_provider

    def active_profile(self) -> LlmProviderProfile:
        return self.profile(self.active_provider())

    def configure(
        self,
        provider: LlmProviderId,
        *,
        key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> LlmProviderProfile:
        state = self.load_state()
        if provider == "argo":
            if base_url is not None and validate_custom_endpoint(base_url) != ARGO_BASE_URL:
                raise ValueError("ARGO endpoint is fixed")
            if model is not None and validate_model_name(model) != self.settings.argo.model:
                raise ValueError("ARGO model is configured by the application")
        else:
            next_base_url = validate_custom_endpoint(base_url or state.custom_base_url or "")
            next_model = validate_model_name(model or state.custom_model or "")
            state = state.model_copy(
                update={"custom_base_url": next_base_url, "custom_model": next_model}
            )
            self._save_state(state)
        if key is not None:
            self.key_store(provider).save(key)
        return self.profile(provider)

    def delete(self, provider: LlmProviderId) -> LlmProviderProfile:
        self.key_store(provider).delete()
        state = self.load_state()
        if provider == "custom":
            state = state.model_copy(update={"custom_base_url": None, "custom_model": None})
            if state.active_provider == "custom":
                state = state.model_copy(update={"active_provider": "argo"})
            self._save_state(state)
        return self.profile(provider)

    def activate(self, provider: LlmProviderId) -> LlmProviderProfile:
        profile = self.profile(provider)
        if not profile.base_url or not profile.model or not profile.key_configured:
            raise ValueError("Selected LLM provider is not fully configured")
        state = self.load_state().model_copy(update={"active_provider": provider})
        self._save_state(state)
        return self.profile(provider)


def migrate_legacy_argo_key(settings: Settings) -> bool:
    """The existing ARGO DPAPI path is already canonical; report whether it exists."""

    expected = (settings.paths.data_dir / ARGO_SECRET_RELATIVE_PATH).resolve()
    return ArgoKeyStore(settings).path == expected and expected.is_file()


def active_llm_model(settings: Settings) -> str:
    return LlmProviderStore(settings).active_profile().model
