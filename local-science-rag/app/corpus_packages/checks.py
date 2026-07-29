"""Persistent, throttled checks of locally synchronized corpus metadata."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.corpus_packages.updates import (
    LatestState,
    UpdateComparison,
    compare_corpus_versions,
    read_latest_manifest,
)


class CorpusUpdateCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checked_at: datetime
    comparison: UpdateComparison
    published_at: datetime | None = None


def update_check_path(settings: Settings) -> Path:
    return settings.paths.cache_dir / "corpus-updates" / "last-check.json"


def invalidate_update_check(settings: Settings) -> None:
    update_check_path(settings).unlink(missing_ok=True)


def _read_cached_check(settings: Settings) -> CorpusUpdateCheck | None:
    path = update_check_path(settings)
    if not path.is_file():
        return None
    try:
        return CorpusUpdateCheck.model_validate_json(path.read_bytes())
    except (OSError, ValueError):
        return None


def _write_cached_check(settings: Settings, check: CorpusUpdateCheck) -> None:
    destination = update_check_path(settings)
    temporary = destination.with_name(f".last-check.{uuid.uuid4().hex[:8]}.tmp")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(check.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    except OSError:
        pass
    finally:
        temporary.unlink(missing_ok=True)


def _unavailable_check(settings: Settings, checked_at: datetime) -> CorpusUpdateCheck:
    try:
        installed = compare_corpus_versions(settings).installed_version
    except Exception:
        installed = None
    return CorpusUpdateCheck(
        checked_at=checked_at,
        comparison=UpdateComparison(
            latest_state=LatestState.SYNC_UNAVAILABLE,
            installed_version=installed,
            available_version=None,
            update_available=False,
            download_required=False,
            message=(
                "Le dossier OneDrive/SharePoint est indisponible. Le corpus local reste utilisable."
            ),
        ),
    )


def refresh_corpus_update_if_due(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> CorpusUpdateCheck:
    """Read synchronized metadata at most once per configured interval."""

    checked_at = now or datetime.now(UTC)
    cached = _read_cached_check(settings)
    interval = timedelta(hours=settings.distribution.check_interval_hours)
    if cached is not None and checked_at - cached.checked_at < interval:
        return cached
    try:
        comparison = compare_corpus_versions(settings)
        latest = read_latest_manifest(settings)
        result = CorpusUpdateCheck(
            checked_at=checked_at,
            comparison=comparison,
            published_at=latest.manifest.published_at if latest.manifest else None,
        )
    except Exception:
        result = _unavailable_check(settings, checked_at)
    _write_cached_check(settings, result)
    return result
