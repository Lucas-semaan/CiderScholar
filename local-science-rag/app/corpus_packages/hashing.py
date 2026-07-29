"""Streaming digests for corpus-package files."""

from __future__ import annotations

import hashlib
from pathlib import Path

_HASH_BLOCK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    """Return a SHA-256 hex digest without loading the complete file into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(_HASH_BLOCK_SIZE):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    """Return the SHA-256 hex digest of an already materialized payload."""

    return hashlib.sha256(payload).hexdigest()
