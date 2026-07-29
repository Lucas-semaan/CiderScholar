"""Allowlisted common-corpus package layout with fail-closed exclusions."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings

INCLUDED_TOP_LEVEL = frozenset({"database", "pdf", "qdrant"})
EXCLUDED_NAMES = frozenset(
    {
        ".env",
        "config.yaml",
        "secrets",
        "private",
        "cache",
        "extracted",
        "conversations",
    }
)
EXCLUDED_SUFFIXES = ("-wal", "-shm", ".key", ".lock", ".tmp")


class CorpusPackageLayoutError(RuntimeError):
    """An allowlisted corpus artifact is unsafe or misplaced."""


def _is_excluded(relative: Path) -> bool:
    lowered_parts = {part.casefold() for part in relative.parts}
    name = relative.name.casefold()
    return bool(
        lowered_parts.intersection(EXCLUDED_NAMES)
        or name.endswith(EXCLUDED_SUFFIXES)
        or "secret" in name
    )


def common_package_files(settings: Settings) -> list[Path]:
    """Select regular, non-symlink files from the explicit common package allowlist."""

    root = settings.paths.common_dir.resolve()
    selected: list[Path] = []
    for top_level in sorted(INCLUDED_TOP_LEVEL):
        candidate = root / top_level
        if not candidate.exists():
            continue
        for path in sorted(item for item in candidate.rglob("*") if item.is_file()):
            if path.is_symlink():
                raise CorpusPackageLayoutError(f"symbolic link is forbidden: {path}")
            relative = path.relative_to(root)
            if _is_excluded(relative):
                continue
            selected.append(path)
    return selected


def package_relative_path(settings: Settings, path: Path) -> str:
    """Return a POSIX package path and reject files outside the common root."""

    try:
        relative = path.resolve().relative_to(settings.paths.common_dir.resolve())
    except ValueError as exc:
        raise CorpusPackageLayoutError("package artifact is outside the common corpus") from exc
    if not relative.parts or relative.parts[0] not in INCLUDED_TOP_LEVEL:
        raise CorpusPackageLayoutError("package artifact is outside the allowlist")
    return relative.as_posix()
