"""Deterministic response-style selection from the current user request."""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

SCIENTIFIC_PROSE_INSTRUCTION = (
    "Commence par les résultats scientifiques, sans reformuler la question ni annoncer le plan. "
    "Une introduction n'est utile que pour lever une ambiguïté réelle. N'emploie pas les "
    "libellés preuve directe ou preuve indirecte. Intègre naturellement la matrice de chaque "
    "étude au résultat cité, uniquement lorsqu'elle est identifiable dans les passages fournis. "
    "Distingue les matrices multiples ; ne devine jamais une matrice inconnue. Précise les "
    "limites de transposition utiles sans avertissement répétitif. Consacre le texte aux "
    "résultats, mécanismes, conditions, contradictions et limites étayés. Évite les conclusions "
    "qui répètent la réponse. "
)


class ResponseStyle(StrEnum):
    """Closed set of layouts ARGO may select for a scientific synthesis."""

    PROSE = "prose"
    THEMATIC_SECTIONS = "thematic_sections"
    COMPARISON = "comparison"
    PROCESS = "process"
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

    return requested_response_style(question) or ResponseStyle.PROSE


def requested_response_style(question: str) -> ResponseStyle | None:
    """Return only an explicit user constraint; otherwise let ARGO choose."""

    normalized = _normalize(question)
    if _contains_phrase(normalized, _BULLET_PROHIBITIONS):
        return ResponseStyle.PROSE
    if _contains_phrase(normalized, _EXPLICIT_LIST_REQUESTS):
        return ResponseStyle.BULLET_LIST
    return None


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    ascii_value = decomposed.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_value.split())


def _contains_phrase(value: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(phrase)}\b", value) for phrase in phrases)
