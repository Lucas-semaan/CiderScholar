"""Hashed private-corpus backup and restore without common-corpus access."""

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


class PrivateBackupError(RuntimeError):
    """A private backup is incomplete, corrupt or unsafe to restore."""


class PrivateBackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: int = Field(default=1, ge=1, le=1)
    created_at: datetime
    files: dict[str, str]


def _snapshot_private_tree(settings: Settings, destination: Path) -> None:
    private_root = settings.paths.private_dir
    if private_root.exists():
        shutil.copytree(private_root, destination)
    else:
        destination.mkdir(parents=True)
    database_path = settings.paths.private_database_path
    if not database_path.is_file():
        return
    snapshot_path = destination / database_path.relative_to(private_root)
    snapshot_path.unlink(missing_ok=True)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(database_path)) as source,
        closing(sqlite3.connect(snapshot_path)) as target,
    ):
        source.backup(target)
    snapshot_path.with_name(f"{snapshot_path.name}-wal").unlink(missing_ok=True)
    snapshot_path.with_name(f"{snapshot_path.name}-shm").unlink(missing_ok=True)


def create_private_backup(settings: Settings, destination: Path | None = None) -> Path:
    """Create an atomic ZIP containing only a consistent private tree snapshot."""

    backup_dir = settings.paths.data_dir / "backups" / "private"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = destination or backup_dir / f"private-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.zip"
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_archive = target.with_name(f".{target.name}.{uuid.uuid4()}.tmp")
    settings.paths.cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.paths.cache_dir) as temporary:
        snapshot = Path(temporary) / "private"
        _snapshot_private_tree(settings, snapshot)
        if any(path.is_symlink() for path in snapshot.rglob("*")):
            raise PrivateBackupError("private backup cannot contain symbolic links")
        hashes = {f"private/{name}": digest for name, digest in directory_hashes(snapshot).items()}
        manifest = PrivateBackupManifest(
            created_at=datetime.now(UTC),
            files=hashes,
        )
        try:
            with zipfile.ZipFile(
                temporary_archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                archive.writestr(
                    "manifest.json",
                    manifest.model_dump_json(indent=2),
                )
                for path in sorted(item for item in snapshot.rglob("*") if item.is_file()):
                    archive.write(path, f"private/{path.relative_to(snapshot).as_posix()}")
            temporary_archive.replace(target)
        finally:
            temporary_archive.unlink(missing_ok=True)
    return target


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    for member in members:
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise PrivateBackupError("private backup contains an unsafe path")
        if stat.S_ISLNK(member.external_attr >> 16):
            raise PrivateBackupError("private backup contains a symbolic link")
        if member.filename != "manifest.json" and path.parts[:1] != ("private",):
            raise PrivateBackupError("private backup contains data outside the private scope")
    return members


def _validated_restore_tree(archive_path: Path, extraction_root: Path) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        members = _safe_members(archive)
        try:
            manifest = PrivateBackupManifest.model_validate_json(archive.read("manifest.json"))
        except (KeyError, ValueError) as exc:
            raise PrivateBackupError("private backup manifest is invalid") from exc
        archive.extractall(extraction_root, members=members)
    restored = extraction_root / "private"
    hashes = {f"private/{name}": digest for name, digest in directory_hashes(restored).items()}
    if hashes != manifest.files:
        raise PrivateBackupError("private backup hashes do not match its manifest")
    return restored


def restore_private_backup(settings: Settings, archive_path: Path) -> Path | None:
    """Restore only the private tree, retaining the replaced version for rollback."""

    archive = archive_path.resolve()
    if not archive.is_file():
        raise PrivateBackupError(f"private backup is unavailable: {archive}")
    settings.paths.cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.paths.cache_dir) as temporary:
        restored = _validated_restore_tree(archive, Path(temporary))
        private_root = settings.paths.private_dir
        rollback_root = settings.paths.data_dir / "backups" / "private" / "rollback"
        rollback_root.mkdir(parents=True, exist_ok=True)
        previous = rollback_root / f"private-{uuid.uuid4()}" if private_root.exists() else None
        if previous is not None:
            private_root.replace(previous)
        try:
            restored.replace(private_root)
        except Exception:
            if previous is not None and previous.exists() and not private_root.exists():
                previous.replace(private_root)
            raise
    return previous
