"""Filesystem-safe activation of the authoritative corpus."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.corpora import CorpusScope, LocalProfile, authorize_corpus_mutation


class CommonCorpusSwapError(RuntimeError):
    """A prepared common corpus cannot be activated safely."""


@dataclass(frozen=True, slots=True)
class CommonCorpusSwap:
    activated_path: Path
    previous_path: Path | None


def directory_hashes(root: Path) -> dict[str, str]:
    """Hash every regular file below a root using stable relative names."""

    if not root.exists():
        return {}
    hashes: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        hashes[path.relative_to(root).as_posix()] = digest.hexdigest()
    return hashes


def activate_prepared_common_corpus(
    settings: Settings,
    prepared_root: Path,
    *,
    profile: LocalProfile,
) -> CommonCorpusSwap:
    """Atomically swap a staged common directory and retain the previous version."""

    authorize_corpus_mutation(CorpusScope.COMMON, profile)
    data_root = settings.paths.data_dir.resolve()
    common_root = settings.paths.common_dir.resolve()
    prepared = prepared_root.resolve()
    if not prepared.is_dir() or not prepared.is_relative_to(data_root):
        raise CommonCorpusSwapError("prepared common corpus must be a directory under data_dir")
    if (
        prepared == common_root
        or common_root.is_relative_to(prepared)
        or prepared.is_relative_to(common_root)
    ):
        raise CommonCorpusSwapError("prepared common corpus overlaps an active corpus")

    archive_root = data_root / "common-archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    previous = archive_root / f"common-{uuid.uuid4()}" if common_root.exists() else None
    if previous is not None:
        common_root.replace(previous)
    try:
        prepared.replace(common_root)
    except Exception:
        if previous is not None and previous.exists() and not common_root.exists():
            previous.replace(common_root)
        raise
    return CommonCorpusSwap(activated_path=common_root, previous_path=previous)
