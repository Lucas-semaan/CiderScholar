"""Current-user LDAP credential persistence backed by Windows DPAPI."""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.config import Settings
from app.secrets import (
    delete_user_environment_value,
    hydrate_user_environment,
    load_protected_user_secret,
    persist_protected_user_secret,
    persist_user_environment_value,
)


@dataclass(frozen=True)
class PublisherCredentials:
    username: str
    password: str


class PublisherCredentialStore:
    def __init__(self, settings: Settings) -> None:
        self.config = settings.publisher_access

    def configured(self) -> bool:
        hydrate_user_environment([self.config.username_env, self.config.password_env])
        return bool(
            os.environ.get(self.config.username_env) and os.environ.get(self.config.password_env)
        )

    def save(self, *, username: str, password: str) -> None:
        cleaned_username = username.strip()
        if not cleaned_username or not password:
            raise ValueError("LDAP username and password are required")
        persist_user_environment_value(self.config.username_env, cleaned_username)
        try:
            persist_protected_user_secret(self.config.password_env, password)
        except Exception:
            delete_user_environment_value(self.config.username_env)
            raise

    def load(self) -> PublisherCredentials:
        hydrate_user_environment([self.config.username_env, self.config.password_env])
        username = os.environ.get(self.config.username_env, "").strip()
        password = load_protected_user_secret(self.config.password_env)
        if not username or not password:
            raise RuntimeError("LDAP credentials are not configured")
        return PublisherCredentials(username=username, password=password)

    def delete(self) -> None:
        delete_user_environment_value(self.config.username_env)
        delete_user_environment_value(self.config.password_env)
