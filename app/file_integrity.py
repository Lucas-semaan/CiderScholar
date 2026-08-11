"""Shared streaming file-integrity primitives."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

HASH_BLOCK_SIZE = 1024 * 1024


def sha256_stream(stream: BinaryIO) -> str:
    """Return a SHA-256 digest without materializing the complete stream."""

    digest = hashlib.sha256()
    while block := stream.read(HASH_BLOCK_SIZE):
        digest.update(block)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    """Return a SHA-256 digest without loading the complete file into memory."""

    with path.open("rb") as stream:
        return sha256_stream(stream)
