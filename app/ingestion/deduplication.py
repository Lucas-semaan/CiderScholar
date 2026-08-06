"""Streaming file fingerprints for exact duplicate detection."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path


def sha256_file(path: str | Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def normalize_title(title: str) -> str:
    """Build a conservative accent-insensitive title key with word boundaries."""

    normalized = unicodedata.normalize("NFKD", title).casefold()
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_title).split())
