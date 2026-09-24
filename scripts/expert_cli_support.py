"""Small safety and persistence helpers shared by expert-memory CLIs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from app.config import Settings


def resolve_run_dir(settings: Settings, value: Path) -> Path:
    """Resolve a private run directory below the configured exports directory."""

    root = settings.paths.exports_dir.resolve()
    candidate = value if value.is_absolute() else root / value
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("--run-dir must remain under paths.exports_dir") from error
    return resolved


def resolve_output_path(settings: Settings, value: Path | None) -> Path | None:
    """Resolve an optional JSON report below the configured exports directory."""

    if value is None:
        return None
    root = settings.paths.exports_dir.resolve()
    candidate = value if value.is_absolute() else root / value
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("--output must remain under paths.exports_dir") from error
    if resolved.suffix.lower() != ".json":
        raise ValueError("--output must be a JSON file")
    return resolved


def write_json_atomic(path: Path, payload: object) -> None:
    """Write one bounded private artifact without leaving a partial JSON file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
