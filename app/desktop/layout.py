"""Per-user Windows paths kept outside the replaceable application directory."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DesktopPaths:
    root: Path
    config: Path
    data: Path
    runtime: Path
    logs: Path


def desktop_paths(environ: Mapping[str, str] | None = None) -> DesktopPaths:
    values = os.environ if environ is None else environ
    local_app_data = values.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is required for the Windows desktop profile")
    root = Path(local_app_data).resolve() / "CiderScholar" / "UserData"
    return DesktopPaths(
        root=root,
        config=root / "config.yaml",
        data=root / "data",
        runtime=root / "data" / "runtime",
        logs=root / "data" / "logs",
    )


def create_desktop_layout(paths: DesktopPaths) -> None:
    """Create persistent application directories outside program files."""

    for relative in (
        "common",
        "queue",
        "exports",
        "backups",
        "secrets",
        "runtime",
        "logs",
        "models",
        "database",
        "cache",
    ):
        (paths.data / relative).mkdir(parents=True, exist_ok=True)
