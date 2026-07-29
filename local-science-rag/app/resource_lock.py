"""Small cross-process exclusive file lock for local corpus resources."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import BinaryIO

ACTIVE_LOCKS: set[Path] = set()
ACTIVE_LOCKS_GUARD = threading.Lock()


class ResourceBusyError(RuntimeError):
    """A local corpus resource is already open in this or another process."""


class ResourceFileLock:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._stream: BinaryIO | None = None

    def acquire(self) -> None:
        with ACTIVE_LOCKS_GUARD:
            if self.path in ACTIVE_LOCKS:
                raise ResourceBusyError(f"resource is already open: {self.path}")
            ACTIVE_LOCKS.add(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            _lock_stream(stream)
        except Exception as exc:
            stream.close()
            with ACTIVE_LOCKS_GUARD:
                ACTIVE_LOCKS.discard(self.path)
            if isinstance(exc, ResourceBusyError):
                raise
            raise ResourceBusyError(f"resource is already open: {self.path}") from exc
        self._stream = stream

    def release(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                _unlock_stream(stream)
            finally:
                stream.close()
        with ACTIVE_LOCKS_GUARD:
            ACTIVE_LOCKS.discard(self.path)

    def __enter__(self) -> ResourceFileLock:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


if os.name == "nt":
    import msvcrt

    def _lock_stream(stream: BinaryIO) -> None:
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise ResourceBusyError("resource lock is held by another process") from exc

    def _unlock_stream(stream: BinaryIO) -> None:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_stream(stream: BinaryIO) -> None:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ResourceBusyError("resource lock is held by another process") from exc

    def _unlock_stream(stream: BinaryIO) -> None:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def corpus_resource_lock_path(qdrant_path: Path) -> Path:
    return qdrant_path.resolve().parent / ".runtime.lock"
