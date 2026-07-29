"""Deterministic response-style selection from the current user request."""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum


class ResponseStyle(StrEnum):
    """Closed set of response layouts supported by the scientific renderer."""

    PROSE = "prose"
    BULLET_LIST = "bullet_list"


_BULLET_PROHIBITIONS = (
    "sans liste",
    "sans listes",
    "sans puce",
    "sans puces",
    "sans bullet",
    "sans bullets",
    "without a list",
    "without lists",
    "without bullet",
    "without bullets",
    "no list",
    "no lists",
    "no bullet",
    "no bullets",
)
_EXPLICIT_LIST_REQUESTS = (
    "liste",
    "listes",
    "puce",
    "puces",
    "checklist",
    "etape",
    "etapes",
    "list",
    "lists",
    "bullet",
    "bullets",
    "steps",
)


def detect_response_style(question: str) -> ResponseStyle:
    """Return the explicitly requested style, defaulting deterministically to prose."""

    normalized = _normalize(question)
    if _contains_phrase(normalized, _BULLET_PROHIBITIONS):
        return ResponseStyle.PROSE
    if _contains_phrase(normalized, _EXPLICIT_LIST_REQUESTS):
        return ResponseStyle.BULLET_LIST
    return ResponseStyle.PROSE


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    ascii_value = decomposed.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_value.split())


def _contains_phrase(value: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(phrase)}\b", value) for phrase in phrases)
