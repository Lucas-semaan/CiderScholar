"""Verified uninstall backup limited to conversations, jobs and private corpus."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.services.private_backup import create_private_backup


class UninstallBackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: Literal[1] = 1
    created_at: datetime
    files: dict[str, str] = Field(min_length=2, max_length=2)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _database_snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        with closing(sqlite3.connect(destination)):
            return
    with (
        closing(sqlite3.connect(source)) as origin,
        closing(sqlite3.connect(destination)) as target,
    ):
        origin.backup(target)


def create_uninstall_backup(settings: Settings, destination: Path) -> Path:
    """Atomically archive durable queue/chat SQLite plus the hashed private backup."""

    target = destination.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_archive = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    settings.paths.cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.paths.cache_dir) as temporary:
        root = Path(temporary)
        database = root / "conversations-and-jobs.sqlite3"
        private = root / "private-corpus.zip"
        _database_snapshot(settings.paths.database_path, database)
        create_private_backup(settings, private)
        manifest = UninstallBackupManifest(
            created_at=datetime.now(UTC),
            files={database.name: _sha256(database), private.name: _sha256(private)},
        )
        try:
            with zipfile.ZipFile(temporary_archive, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", manifest.model_dump_json(indent=2))
                archive.write(database, database.name)
                archive.write(private, private.name)
            temporary_archive.replace(target)
        finally:
            temporary_archive.unlink(missing_ok=True)
    return target
