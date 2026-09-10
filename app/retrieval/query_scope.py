"""Deterministic, fail-closed scope guard for CiderScholar questions.

The guard is deliberately evaluated before opening SQLite, Qdrant, or a
provider-backed retrieval path. It does not decide whether a question has
enough evidence; it only decides whether it belongs to the cider remit.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

ScopeStatus = Literal["accepted", "rejected"]


@dataclass(frozen=True, slots=True)
class QueryScopeDecision:
    status: ScopeStatus
    reason_code: str

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return normalized.encode("ascii", "ignore").decode("ascii")


_DIRECT_CIDER = re.compile(r"\b(?:hard\s+)?ciders?\b|\bcidres?\b|\bcidricol\w*\b|\bsidras?\b")
_DERIVED_PRODUCTS = re.compile(
    r"\b(?:calvados|pommeau|applejack|apple\s+(?:brandy|spirit|spiritueux)|"
    r"cider\s+brand(?:y|ies)|eau[x]?\s+de\s+vie\s+de\s+cidre)\b"
)
_APPLE_MATERIAL = re.compile(r"\b(?:apple|apples|pomme|pommes|malus)\b")
_CIDER_SCIENCE = re.compile(
    r"\b(?:ferment\w*|malolactic|levur\w*|yeast\w*|microb\w*|bacter\w*|"
    r"oenococc\w*|saccharomyc\w*|polyphenol\w*|phenol\w*|tannin\w*|"
    r"procyanidin\w*|pectin\w*|protein\w*|peptid\w*|nitrogen|azote|"
    r"haze|trouble|clarif\w*|filtrat\w*|pasteuris\w*|press\w*|"
    r"must\b|mout\b|pomace|marc\b|juice|jus\b|distill\w*|"
    r"arom\w*|volatile\w*|astring\w*|oxid\w*|stabili\w*|"
    r"physicochim\w*|biochem\w*|cultivar\w*|variet\w*|verger\w*|"
    r"orchard\w*|recolt\w*|harvest\w*|stockage|storage|elevage|aging|"
    r"maturation|qualite|quality)\b"
)
_EXPLICITLY_OUT_OF_SCOPE = re.compile(
    r"\b(?:meteo|weather|temperature\s+(?:demain|today|tomorrow)|capital(?:e)?|"
    r"president|election|politique|politic\w*|guerre|war|football|sport\w*|"
    r"recette|recipe|restaurant|tourism|voyage|travel|marketing|econom(?:y|ic|ie)|"
    r"bourse|stock\s+market|bitcoin|crypto(?:currency)?|programm(?:ation|ing)|"
    r"python\s+(?:code|program)|javascript|ordinateur|computer|software|"
    r"quantum|astronom\w*|mathemati\w*|algebra|geograph\w*|"
    r"patient\w*|clinical|clinique|cancer|maladie|disease|nutrition|diet\w*|"
    r"blood\s+(?:pressure|glucose)|glycem\w*|allerg\w*|medecin|medicine)\b"
)
_REFERENTIAL_FOLLOW_UP = re.compile(
    r"^(?:et\s+)?(?:pourquoi|comment|lesquels?|lesquelles?|et\s+apres|"
    r"plus\s+de\s+details?|precise|resume|reformule|what\s+about|why|how|"
    r"which|more\s+detail)\b"
)


def _has_scientific_cider_signal(question: str) -> bool:
    return bool(
        _DIRECT_CIDER.search(question)
        or _DERIVED_PRODUCTS.search(question)
        or (_APPLE_MATERIAL.search(question) and _CIDER_SCIENCE.search(question))
        or _CIDER_SCIENCE.search(question)
    )


def classify_query_scope(
    message: str,
    history: Sequence[Mapping[str, str]] = (),
) -> QueryScopeDecision:
    """Classify a chat question without making a retrieval request.

    A technical term used in cider science (for example ``fermentation``) is
    accepted because concise follow-up questions commonly omit "cider". A
    clearly foreign subject always wins, including if an earlier turn concerned
    cider. Very short referential follow-ups inherit an in-scope user question.
    """

    current = _fold(" ".join(message.split()))
    if _EXPLICITLY_OUT_OF_SCOPE.search(current):
        return QueryScopeDecision("rejected", "clearly_foreign_topic")
    if _has_scientific_cider_signal(current):
        return QueryScopeDecision("accepted", "cider_science_signal")

    prior_questions = [
        _fold(" ".join(item.get("content", "").split()))
        for item in history
        if item.get("role") == "user" and item.get("content", "").strip()
    ]
    if (
        len(current.split()) <= 12
        and _REFERENTIAL_FOLLOW_UP.search(current)
        and any(_has_scientific_cider_signal(question) for question in prior_questions[-2:])
    ):
        return QueryScopeDecision("accepted", "in_scope_follow_up")
    return QueryScopeDecision("rejected", "no_cider_science_signal")
