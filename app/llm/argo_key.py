"""Current-user ARGO key persistence outside exports and synchronized folders."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.llm.argo_client import ArgoHealth
from app.secrets import DpapiFileSecretStore

ARGO_SECRET_RELATIVE_PATH = Path("secrets") / "argo-key.dpapi"
MAX_ARGO_KEY_CHARACTERS = 4096


class ArgoKeyStatus(BaseModel):
    """Only safe key metadata exposed outside the persistence service."""

    model_config = ConfigDict(extra="forbid")

    configured: bool


class ArgoConnectionStatus(BaseModel):
    """Actionable public outcome of a bounded ARGO model probe."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["ready", "missing", "rejected", "network_unavailable", "model_unavailable"]
    configured: bool
    message: str


class ArgoKeyStore(DpapiFileSecretStore):
    """DPAPI store rooted in local application data, never in the export tree."""

    def __init__(self, settings: Settings) -> None:
        path = (settings.paths.data_dir / ARGO_SECRET_RELATIVE_PATH).resolve()
        exports_dir = settings.paths.exports_dir.resolve()
        if path == exports_dir or path.is_relative_to(exports_dir):
            raise ValueError("ARGO secret path must remain outside exports")
        super().__init__(path, description="CiderScholar ARGO API key")

    def save(self, secret: str) -> None:
        super().save(validate_argo_key(secret))

    def status(self) -> ArgoKeyStatus:
        return ArgoKeyStatus(configured=self.configured())


def validate_argo_key(secret: str) -> str:
    """Normalize harmless edge whitespace and reject malformed key material."""

    cleaned = secret.strip()
    if not cleaned:
        raise ValueError("ARGO key cannot be empty")
    if len(cleaned) > MAX_ARGO_KEY_CHARACTERS:
        raise ValueError("ARGO key is too long")
    if any(character.isspace() for character in cleaned):
        raise ValueError("ARGO key cannot contain internal whitespace")
    return cleaned


def argo_connection_status(
    *,
    key_configured: bool,
    health: ArgoHealth | None,
) -> ArgoConnectionStatus:
    if not key_configured or health is None:
        return ArgoConnectionStatus(
            state="missing",
            configured=False,
            message="Aucune clé ARGO n'est enregistrée. Ajoutez-la dans les paramètres.",
        )
    if health.reachable and health.model_available:
        return ArgoConnectionStatus(
            state="ready",
            configured=True,
            message="La clé ARGO et le modèle configuré sont accessibles.",
        )
    if health.reachable:
        return ArgoConnectionStatus(
            state="model_unavailable",
            configured=True,
            message=(
                "La clé est acceptée, mais le modèle configuré n'est pas accessible pour ce compte."
            ),
        )
    if "rejected" in (health.error or "").casefold():
        return ArgoConnectionStatus(
            state="rejected",
            configured=True,
            message="La clé ARGO a été refusée. Remplacez-la puis relancez le test.",
        )
    return ArgoConnectionStatus(
        state="network_unavailable",
        configured=True,
        message="ARGO est inaccessible. Vérifiez le réseau INRAE ou le VPN puis réessayez.",
    )
