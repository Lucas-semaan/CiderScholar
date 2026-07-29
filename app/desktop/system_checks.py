"""Early Windows, architecture and disk-capacity installation checks."""

from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

WINDOWS_11_MINIMUM_BUILD = 22000
INSTALLATION_MARGIN_BYTES = 2 * 1024**3


class DesktopCompatibilityError(RuntimeError):
    """The host cannot safely run the packaged desktop application."""


@dataclass(frozen=True, slots=True)
class WindowsHost:
    system: str
    machine: str
    build: int


@dataclass(frozen=True, slots=True)
class DiskCapacity:
    required_bytes: int
    free_bytes: int

    @property
    def sufficient(self) -> bool:
        return self.free_bytes >= self.required_bytes


def detected_windows_host() -> WindowsHost:
    build = 0
    if sys.platform == "win32":
        build = int(sys.getwindowsversion().build)
    return WindowsHost(system=platform.system(), machine=platform.machine(), build=build)


def validate_windows_11_x64(host: WindowsHost | None = None) -> WindowsHost:
    detected = host or detected_windows_host()
    if detected.system.casefold() != "windows":
        raise DesktopCompatibilityError("CiderScholar requires Windows 11")
    if detected.machine.casefold() not in {"amd64", "x86_64"}:
        raise DesktopCompatibilityError("CiderScholar requires a 64-bit x86 processor")
    if detected.build < WINDOWS_11_MINIMUM_BUILD:
        raise DesktopCompatibilityError("CiderScholar requires Windows 11 build 22000 or newer")
    return detected


def required_installation_bytes(*sizes: int) -> int:
    if any(size < 0 for size in sizes):
        raise ValueError("installation component sizes cannot be negative")
    return sum(sizes) + INSTALLATION_MARGIN_BYTES


def check_disk_capacity(
    destination: Path,
    required_bytes: int,
    *,
    free_bytes: int | None = None,
) -> DiskCapacity:
    available = (
        shutil.disk_usage(destination.resolve().anchor).free if free_bytes is None else free_bytes
    )
    result = DiskCapacity(required_bytes=required_bytes, free_bytes=available)
    if not result.sufficient:
        required_gb = required_bytes / 1024**3
        free_gb = available / 1024**3
        raise DesktopCompatibilityError(
            f"Espace disque insuffisant : {required_gb:.1f} Go requis, {free_gb:.1f} Go libres"
        )
    return result
