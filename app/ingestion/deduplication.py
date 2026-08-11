"""Streaming file fingerprints for exact duplicate detection."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path

MIN_SPECIFIC_TITLE_CHARACTERS = 40
MIN_SPECIFIC_TITLE_WORDS = 5
GENERIC_TITLES = {
    "accepted manuscript",
    "article",
    "document",
    "front matter",
    "full text",
    "main document",
    "microsoft word document",
    "publication",
    "research article",
    "untitled",
}


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


def is_specific_title(title: str) -> bool:
    """Return whether a normalized title is safe for candidate selection only."""

    title_key = normalize_title(title)
    return (
        len(title_key) >= MIN_SPECIFIC_TITLE_CHARACTERS
        and len(title_key.split()) >= MIN_SPECIFIC_TITLE_WORDS
        and title_key not in GENERIC_TITLES
    )


def normalized_document_sha256(page_texts: Iterable[str]) -> str:
    """Fingerprint extracted page text while preserving page boundaries.

    This deliberately ignores PDF container metadata and whitespace-only rendering
    differences.  It is an exact normalized-text check, not a similarity score.
    """

    digest = hashlib.sha256()
    for page_number, text in enumerate(page_texts, start=1):
        normalized = unicodedata.normalize("NFKC", text).casefold()
        normalized = " ".join(normalized.split())
        digest.update(str(page_number).encode("ascii"))
        digest.update(b"\x00")
        digest.update(normalized.encode("utf-8"))
        digest.update(b"\x00\xff")
    return digest.hexdigest()
