"""Small testable primitives used by the Windows process supervisor."""

from __future__ import annotations

import ctypes
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


class SupervisorError(RuntimeError):
    """The desktop API and worker could not be kept in one coherent lifecycle."""


class NamedWindowsMutex:
    """Current-user named mutex preventing duplicate local service stacks."""

    ERROR_ALREADY_EXISTS = 183

    def __init__(self, name: str = "Local\\CiderScholar.Desktop.0.1") -> None:
        self.name = name
        self._handle: int | None = None

    def acquire(self) -> bool:
        if os.name != "nt":
            raise SupervisorError("the desktop mutex is only available on Windows")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = int(handle)
        return ctypes.get_last_error() != self.ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self._handle is not None:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> NamedWindowsMutex:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


def request_shutdown(stop_file: Path) -> Path:
    """Atomically expose a content-free cooperative shutdown marker."""

    destination = stop_file.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(
            json.dumps({"schema_version": 1, "requested": True}) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def wait_for_health(url: str, *, timeout_seconds: float = 60.0) -> bool:
    """Poll real readiness instead of relying on a fixed launch delay."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                payload = json.loads(response.read())
            if response.status == 200 and payload.get("status") == "ok":
                return True
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    return False
