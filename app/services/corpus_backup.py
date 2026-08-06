"""Verified backup and restore of the single scientific corpus."""

from __future__ import annotations

import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.services.corpus_updates import directory_hashes


class CorpusBackupError(RuntimeError):
    """A corpus backup is incomplete, corrupt, or unsafe to restore."""


class CorpusBackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: int = Field(default=1, ge=1, le=1)
    created_at: datetime
    files: dict[str, str]


def _snapshot_corpus_tree(settings: Settings, destination: Path) -> None:
    corpus_root = settings.paths.common_dir
    if corpus_root.exists():
        shutil.copytree(corpus_root, destination)
    else:
        destination.mkdir(parents=True)
    database_path = settings.paths.common_database_path
    if not database_path.is_file():
        return
    snapshot_path = destination / database_path.relative_to(corpus_root)
    snapshot_path.unlink(missing_ok=True)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(database_path)) as source,
        closing(sqlite3.connect(snapshot_path)) as target,
    ):
        source.backup(target)
    snapshot_path.with_name(f"{snapshot_path.name}-wal").unlink(missing_ok=True)
    snapshot_path.with_name(f"{snapshot_path.name}-shm").unlink(missing_ok=True)


def create_corpus_backup(settings: Settings, destination: Path | None = None) -> Path:
    """Create an atomic ZIP containing a consistent corpus snapshot."""

    backup_dir = settings.paths.data_dir / "backups" / "corpus"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = destination or backup_dir / f"corpus-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.zip"
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_archive = target.with_name(f".{target.name}.{uuid.uuid4()}.tmp")
    settings.paths.cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.paths.cache_dir) as temporary:
        snapshot = Path(temporary) / "corpus"
        _snapshot_corpus_tree(settings, snapshot)
        if any(path.is_symlink() for path in snapshot.rglob("*")):
            raise CorpusBackupError("corpus backup cannot contain symbolic links")
        hashes = {f"corpus/{name}": digest for name, digest in directory_hashes(snapshot).items()}
        manifest = CorpusBackupManifest(created_at=datetime.now(UTC), files=hashes)
        try:
            with zipfile.ZipFile(
                temporary_archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                archive.writestr("manifest.json", manifest.model_dump_json(indent=2))
                for path in sorted(item for item in snapshot.rglob("*") if item.is_file()):
                    archive.write(path, f"corpus/{path.relative_to(snapshot).as_posix()}")
            temporary_archive.replace(target)
        finally:
            temporary_archive.unlink(missing_ok=True)
    return target


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    for member in members:
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise CorpusBackupError("corpus backup contains an unsafe path")
        if stat.S_ISLNK(member.external_attr >> 16):
            raise CorpusBackupError("corpus backup contains a symbolic link")
        if member.filename != "manifest.json" and path.parts[:1] != ("corpus",):
            raise CorpusBackupError("corpus backup contains data outside the corpus")
    return members


def _validated_restore_tree(archive_path: Path, extraction_root: Path) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        members = _safe_members(archive)
        try:
            manifest = CorpusBackupManifest.model_validate_json(archive.read("manifest.json"))
        except (KeyError, ValueError) as exc:
            raise CorpusBackupError("corpus backup manifest is invalid") from exc
        archive.extractall(extraction_root, members=members)
    restored = extraction_root / "corpus"
    hashes = {f"corpus/{name}": digest for name, digest in directory_hashes(restored).items()}
    if hashes != manifest.files:
        raise CorpusBackupError("corpus backup hashes do not match its manifest")
    return restored


def restore_corpus_backup(settings: Settings, archive_path: Path) -> Path | None:
    """Restore the corpus, retaining the replaced version for rollback."""

    archive = archive_path.resolve()
    if not archive.is_file():
        raise CorpusBackupError(f"corpus backup is unavailable: {archive}")
    settings.paths.cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.paths.cache_dir) as temporary:
        restored = _validated_restore_tree(archive, Path(temporary))
        corpus_root = settings.paths.common_dir
        rollback_root = settings.paths.data_dir / "backups" / "corpus" / "rollback"
        rollback_root.mkdir(parents=True, exist_ok=True)
        previous = rollback_root / f"corpus-{uuid.uuid4()}" if corpus_root.exists() else None
        if previous is not None:
            corpus_root.replace(previous)
        try:
            restored.replace(corpus_root)
        except Exception:
            if previous is not None and previous.exists() and not corpus_root.exists():
                previous.replace(corpus_root)
            raise
    return previous
