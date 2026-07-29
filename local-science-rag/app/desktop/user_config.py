"""Atomic non-secret user overrides written by the first-launch assistant."""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from pathlib import Path

import yaml

from app.config import Settings
from app.memory_profiles import MEMORY_PROFILES, MemoryProfileName, apply_memory_profile


class UserConfigurationError(RuntimeError):
    """The packaged base configuration cannot receive a safe user override."""


def packaged_config_path(environ: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    configured = values.get("CIDERSCHOLAR_CONFIG_PATH", "").strip()
    if not configured:
        raise UserConfigurationError("packaged configuration path is unavailable")
    path = Path(configured).resolve()
    if not path.is_file():
        raise UserConfigurationError("packaged configuration file is unavailable")
    return path


def _merge(base: dict[str, object], updates: dict[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in updates.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge(current, value)
        else:
            merged[key] = value
    return merged


def write_user_overrides(config: Path, updates: dict[str, object]) -> Path:
    destination = config.with_name("config.user.yaml")
    current: dict[str, object] = {}
    if destination.is_file():
        loaded = yaml.safe_load(destination.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise UserConfigurationError("existing user configuration is invalid")
        current = loaded
    merged = _merge(current, updates)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(
            yaml.safe_dump(merged, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def memory_profile_overrides(
    settings: Settings,
    profile_name: MemoryProfileName,
) -> tuple[Settings, dict[str, object]]:
    profile = MEMORY_PROFILES[profile_name]
    updated = apply_memory_profile(settings, profile)
    return updated, {
        "memory": updated.memory.model_dump(mode="python"),
        "embeddings": {"batch_size": updated.embeddings.batch_size},
        "retrieval": {"hybrid_candidate_limit": updated.retrieval.hybrid_candidate_limit},
        "evidence": {"candidate_chunks_per_article": updated.evidence.candidate_chunks_per_article},
    }
