"""Streaming file fingerprints for exact duplicate detection."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: str | Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()
