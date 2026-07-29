"""Restart-only atomic activation of a previously validated common corpus."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.corpora import LocalProfile
from app.corpus_packages.checks import invalidate_update_check
from app.corpus_packages.hashing import sha256_file
from app.corpus_packages.installer import (
    CorpusInstallError,
    ReadyCorpusUpdate,
    ready_update_path,
)
from app.corpus_packages.models import CorpusManifest
from app.corpus_packages.updates import InstalledCorpusState, write_installed_state
from app.services.corpus_updates import activate_prepared_common_corpus


class CorpusActivationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_version: str
    active_path: str
    previous_path: str | None


class ScheduledCorpusRollback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previous_path: str
    scheduled_at: datetime


def rollback_marker_path(settings: Settings) -> Path:
    return settings.paths.cache_dir / "corpus-updates" / "rollback.json"


def schedule_previous_rollback(settings: Settings) -> ScheduledCorpusRollback:
    """Select the latest retained corpus and schedule its restart-only activation."""

    archive_root = (settings.paths.data_dir / "common-archive").resolve()
    candidates = sorted(
        (path for path in archive_root.glob("common-*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not candidates:
        raise CorpusInstallError("Aucune version précédente du corpus n'est disponible.")
    scheduled = ScheduledCorpusRollback(
        previous_path=str(candidates[0].resolve()),
        scheduled_at=datetime.now(UTC),
    )
    destination = rollback_marker_path(settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".rollback.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(
            json.dumps(scheduled.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
        ready_update_path(settings).unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)
    return scheduled


def activate_ready_update_at_startup(settings: Settings) -> CorpusActivationReport | None:
    """Activate only a marker produced after full validation; never while the app is running."""

    marker_path = ready_update_path(settings)
    if not marker_path.is_file():
        return None
    try:
        ready = ReadyCorpusUpdate.model_validate_json(marker_path.read_bytes())
        extracted = Path(ready.extracted_directory).resolve()
        manifest_path = Path(ready.manifest_path).resolve()
        expected_root = (settings.paths.cache_dir / "corpus-updates").resolve()
        if not extracted.is_dir() or not extracted.is_relative_to(expected_root):
            raise CorpusInstallError("ready corpus directory is unavailable or outside staging")
        if not manifest_path.is_file() or sha256_file(manifest_path) != ready.manifest_sha256:
            raise CorpusInstallError("ready corpus manifest hash mismatch")
        manifest = CorpusManifest.model_validate_json(manifest_path.read_bytes())
        if manifest.corpus_version != ready.corpus_version:
            raise CorpusInstallError("ready corpus version mismatch")
        database = extracted / "database" / settings.paths.common_database_path.name
        database.with_name(f"{database.name}-wal").unlink(missing_ok=True)
        database.with_name(f"{database.name}-shm").unlink(missing_ok=True)
        (extracted / "qdrant" / ".lock").unlink(missing_ok=True)
        swap = activate_prepared_common_corpus(
            settings,
            extracted,
            profile=LocalProfile.ADMIN,
        )
        write_installed_state(
            settings,
            InstalledCorpusState(
                corpus_version=manifest.corpus_version,
                installed_at=datetime.now(UTC),
                manifest_sha256=ready.manifest_sha256,
            ),
        )
        invalidate_update_check(settings)
        marker_path.unlink()
    except (OSError, ValueError) as exc:
        raise CorpusInstallError("ready corpus activation metadata is invalid") from exc
    return CorpusActivationReport(
        corpus_version=manifest.corpus_version,
        active_path=str(swap.activated_path),
        previous_path=str(swap.previous_path) if swap.previous_path else None,
    )


def rollback_previous_at_startup(
    settings: Settings,
    previous_path: Path,
) -> CorpusActivationReport:
    """Atomically restore one retained common version and archive the replaced active version."""

    archive_root = (settings.paths.data_dir / "common-archive").resolve()
    previous = previous_path.resolve()
    if not previous.is_dir() or not previous.is_relative_to(archive_root):
        raise CorpusInstallError("rollback target is outside retained common versions")
    state_path = previous / "installed.json"
    state = (
        InstalledCorpusState.model_validate_json(state_path.read_bytes())
        if state_path.is_file()
        else None
    )
    swap = activate_prepared_common_corpus(
        settings,
        previous,
        profile=LocalProfile.ADMIN,
    )
    invalidate_update_check(settings)
    return CorpusActivationReport(
        corpus_version=state.corpus_version if state else "unversioned",
        active_path=str(swap.activated_path),
        previous_path=str(swap.previous_path) if swap.previous_path else None,
    )


def apply_scheduled_rollback_at_startup(
    settings: Settings,
) -> CorpusActivationReport | None:
    """Apply a valid rollback marker once, before normal ready-update activation."""

    marker_path = rollback_marker_path(settings)
    if not marker_path.is_file():
        return None
    try:
        scheduled = ScheduledCorpusRollback.model_validate_json(marker_path.read_bytes())
        report = rollback_previous_at_startup(settings, Path(scheduled.previous_path))
        marker_path.unlink()
        return report
    except (OSError, ValueError) as exc:
        if isinstance(exc, CorpusInstallError):
            raise
        raise CorpusInstallError("Le marqueur de retour arrière est invalide.") from exc
