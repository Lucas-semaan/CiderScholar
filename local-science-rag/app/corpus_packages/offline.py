"""Exclusive common-corpus guard used while creating an offline package snapshot."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from app.config import Settings
from app.database.sqlite import Database
from app.resource_lock import ResourceBusyError, ResourceFileLock


class CommonCorpusOfflineGuard:
    """Hold the Qdrant runtime lock and an exclusive SQLite transaction."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = ResourceFileLock(settings.paths.common_dir / ".runtime.lock")
        self.connection: sqlite3.Connection | None = None
        self.wal_checkpointed = False

    def __enter__(self) -> CommonCorpusOfflineGuard:
        self._lock.acquire()
        connection = Database(self.settings.paths.common_database_path).connect()
        connection.execute("PRAGMA busy_timeout = 1000")
        try:
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is None or int(checkpoint[0]) != 0:
                raise ResourceBusyError("common SQLite WAL checkpoint is busy")
            self.wal_checkpointed = True
            connection.execute("BEGIN EXCLUSIVE")
        except (sqlite3.OperationalError, ResourceBusyError) as exc:
            connection.close()
            self._lock.release()
            if isinstance(exc, ResourceBusyError):
                raise
            raise ResourceBusyError("common SQLite database is still in use") from exc
        self.connection = connection
        return self

    def copy_checkpointed_sqlite(self, destination: Path) -> Path:
        if self.connection is None or not self.wal_checkpointed:
            raise RuntimeError("offline corpus guard is not active")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.settings.paths.common_database_path, destination)
        destination.with_name(f"{destination.name}-wal").unlink(missing_ok=True)
        destination.with_name(f"{destination.name}-shm").unlink(missing_ok=True)
        return destination

    def __exit__(self, *_args: object) -> None:
        if self.connection is not None:
            self.connection.rollback()
            self.connection.close()
            self.connection = None
        self._lock.release()
