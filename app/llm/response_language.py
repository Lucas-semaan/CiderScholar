"""Deterministic language guardrails for user-visible scientific prose."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Literal

OutputLanguage = Literal["fr", "en"]

_FRENCH_FUNCTION_WORDS = frozenset(
    {
        "ainsi",
        "avec",
        "aux",
        "ce",
        "ces",
        "cette",
        "comme",
        "dans",
        "des",
        "donc",
        "dont",
        "du",
        "elle",
        "elles",
        "entre",
        "est",
        "et",
        "leur",
        "leurs",
        "les",
        "mais",
        "ne",
        "pas",
        "par",
        "pour",
        "que",
        "qui",
        "sans",
        "selon",
        "sont",
        "sur",
        "une",
    }
)
_ENGLISH_FUNCTION_WORDS = frozenset(
    {
        "and",
        "are",
        "because",
        "but",
        "does",
        "during",
        "for",
        "from",
        "has",
        "have",
        "into",
        "is",
        "may",
        "of",
        "that",
        "the",
        "their",
        "these",
        "this",
        "those",
        "to",
        "was",
        "were",
        "which",
        "while",
        "with",
        "without",
    }
)
_FRENCH_CONTENT_WORDS = frozenset(
    {
        "affecte",
        "augmente",
        "augmentation",
        "arome",
        "aromes",
        "bois",
        "diminue",
        "diminution",
        "effet",
        "effets",
        "etude",
        "etudes",
        "limite",
        "limites",
        "levure",
        "levures",
        "montre",
        "observe",
        "observee",
        "observes",
        "preuve",
        "preuves",
        "pomme",
        "pommes",
        "resultat",
        "resultats",
        "vieillissement",
    }
)
_ENGLISH_CONTENT_WORDS = frozenset(
    {
        "affected",
        "aging",
        "apple",
        "apples",
        "decrease",
        "decreased",
        "decreases",
        "effect",
        "effects",
        "evidence",
        "increase",
        "increased",
        "increases",
        "limitation",
        "limitations",
        "observed",
        "oak",
        "result",
        "results",
        "showed",
        "shows",
        "studies",
        "study",
        "wood",
        "yeast",
        "yeasts",
    }
)
_FRENCH_QUESTION_WORDS = frozenset(
    {
        "comment",
        "est",
        "impact",
        "influence",
        "peut",
        "pourquoi",
        "quel",
        "quelle",
        "quelles",
        "quels",
    }
)
_ENGLISH_QUESTION_WORDS = frozenset(
    {
        "can",
        "could",
        "does",
        "how",
        "impact",
        "influence",
        "what",
        "when",
        "which",
        "why",
        "would",
    }
)
_PROTECTED_SCIENTIFIC_PHRASES = re.compile(
    r"\b(?:ex\s+vivo|in\s+silico|in\s+situ|in\s+vitro|in\s+vivo|et\s+al)\b",
    re.IGNORECASE,
)


def _plain_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _tokens(value: str) -> list[str]:
    unprotected = _PROTECTED_SCIENTIFIC_PHRASES.sub(" ", value)
    return re.findall(r"[a-z]+", _plain_text(unprotected))


def _signals(value: str) -> tuple[int, int, int, int]:
    words = _tokens(value)
    return (
        sum(word in _FRENCH_FUNCTION_WORDS for word in words),
        sum(word in _ENGLISH_FUNCTION_WORDS for word in words),
        sum(word in _FRENCH_CONTENT_WORDS for word in words),
        sum(word in _ENGLISH_CONTENT_WORDS for word in words),
    )


def question_language(question: str) -> OutputLanguage:
    """Infer the supported output language from the current question, never chat history."""

    words = _tokens(question)
    french_function, english_function, french_content, english_content = _signals(question)
    french = (
        french_function
        + (2 * french_content)
        + (3 * sum(word in _FRENCH_QUESTION_WORDS for word in words))
    )
    english = (
        english_function
        + (2 * english_content)
        + (3 * sum(word in _ENGLISH_QUESTION_WORDS for word in words))
    )
    return "en" if english > french else "fr"


def output_language_name(language: OutputLanguage) -> str:
    return "français" if language == "fr" else "English"


def validate_output_language(question: str, elements: Sequence[str]) -> None:
    """Reject a clear language switch in any individual user-visible prose element.

    Source excerpts and bibliographic metadata must not be passed here: they remain verbatim.
    Common Latin scientific phrases are ignored so that they do not look like English prose.
    """

    expected = question_language(question)
    for index, element in enumerate(elements):
        french_function, english_function, french_content, english_content = _signals(element)
        if expected == "fr":
            foreign_content = english_content > 0
            foreign_grammar = english_function >= 1
        else:
            foreign_content = french_content > 0
            foreign_grammar = french_function >= 1
        if foreign_content or foreign_grammar:
            raise RuntimeError(
                "ARGO returned output element "
                f"{index + 1} in a language different from the user's question"
            )
