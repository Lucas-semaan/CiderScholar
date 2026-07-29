"""Inspectable bilingual query variants derived without evaluation labels."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.retrieval.scientific_intent import (
    analyze_scientific_intent,
    intent_query_variants,
)


class BilingualLexiconEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fr: str = Field(min_length=1, max_length=80)
    en: str = Field(min_length=1, max_length=80)


class QueryVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=2, max_length=2000)
    language: Literal["fr", "en", "mixed"]
    derivation: Literal["original", "lexicon_swap", "matched_terms", "argo_plan"]
    matched_terms: list[str] = Field(default_factory=list, max_length=20)
    anchor_terms: list[str] = Field(default_factory=list, max_length=12)
    scope_tier: Literal["strict", "near_matrix", "model_matrix"] = "strict"


DEFAULT_CIDER_LEXICON: tuple[BilingualLexiconEntry, ...] = tuple(
    BilingualLexiconEntry(fr=fr, en=en)
    for fr, en in (
        ("cidre", "cider"),
        ("jus de pomme", "apple juice"),
        ("occurrence", "occurrence"),
        ("prévalence", "prevalence"),
        ("pasteurisé", "pasteurized"),
        ("pasteurisation", "pasteurization"),
        ("fermentation", "fermentation"),
        ("levure", "yeast"),
        ("bactérie lactique", "lactic acid bacterium"),
        ("bactérie acétique", "acetic acid bacterium"),
        ("altération microbienne", "microbial spoilage"),
        ("moisissure", "mold"),
        ("patuline", "patulin"),
        ("contaminant", "contaminant"),
        ("polyphénol", "polyphenol"),
        ("composé volatil", "volatile compound"),
        ("oxygénation", "oxygenation"),
        ("azote assimilable", "yeast assimilable nitrogen"),
    )
)

_SCIENTIFIC_ANCHOR_PATTERN = re.compile(r"(?<![\w-])[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]{4,}(?![\w-])")
_ANCHOR_STOPWORDS = frozenset(
    {
        "comment",
        "dans",
        "quelle",
        "quelles",
        "quel",
        "quels",
        "pourquoi",
        "question",
    }
)
_OCCURRENCE_INTENT = frozenset(
    {
        "contamination",
        "detection",
        "distribution",
        "frequency",
        "frequence",
        "incidence",
        "isolement",
        "isolation",
        "occurrence",
        "presence",
        "prevalence",
        "survey",
    }
)
_PROCESS_QUALIFIERS = frozenset(
    {
        "pasteurise",
        "pasteurisee",
        "pasteurises",
        "pasteurisees",
        "pasteurization",
        "pasteurized",
        "pasteurisation",
    }
)
_MATRIX_TERMS = frozenset({"apple juice", "jus de pomme"})
_OCCURRENCE_TERMS_FR = (
    "occurrence",
    "prévalence",
    "incidence",
    "distribution",
    "enquête",
    "détection",
    "isolement",
    "contamination",
    "fréquence",
)
_OCCURRENCE_TERMS_EN = (
    "occurrence",
    "prevalence",
    "incidence",
    "distribution",
    "survey",
    "detection",
    "isolation",
    "contamination",
    "frequency",
)
_FACET_QUERY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "structure": ("phenolics", "acidity", "mouthfeel"),
}


def _replace_phrase(text: str, source: str, destination: str) -> tuple[str, bool]:
    pattern = re.compile(rf"(?<!\w){re.escape(source)}(?!\w)", flags=re.IGNORECASE)
    replaced, count = pattern.subn(destination, text)
    return replaced, count > 0


def _plain_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _scientific_anchors(question: str) -> list[str]:
    return list(
        dict.fromkeys(
            match.group(0)
            for match in _SCIENTIFIC_ANCHOR_PATTERN.finditer(question)
            if match.start() > 0
            for token in (match.group(0),)
            if _plain_text(token) not in _ANCHOR_STOPWORDS
        )
    )[:12]


def _has_occurrence_intent(question: str) -> bool:
    terms = set(re.findall(r"[^\W_]+", _plain_text(question)))
    return bool(terms.intersection(_OCCURRENCE_INTENT))


def _has_process_qualifier(question: str) -> bool:
    terms = set(re.findall(r"[^\W_]+", _plain_text(question)))
    return bool(terms.intersection(_PROCESS_QUALIFIERS))


def _has_apple_juice_matrix(question: str) -> bool:
    normalized = _plain_text(question)
    return any(_plain_text(term) in normalized for term in _MATRIX_TERMS)


def variant_matches_text(
    variant: QueryVariant,
    text: str,
    *,
    title: str = "",
) -> bool:
    """Keep high-signal scientific anchors in every controlled expansion."""

    if not variant.anchor_terms:
        return True
    haystack = _plain_text(f"{title}\n{text}")
    return any(_plain_text(anchor) in haystack for anchor in variant.anchor_terms)


def query_variant_weight(variant: QueryVariant) -> float:
    """Prefer the requested matrix while retaining explicit, labelled fallbacks."""

    return {
        "strict": 1.0,
        "near_matrix": 0.65,
        "model_matrix": 0.35,
    }[variant.scope_tier]


def build_bilingual_variants(
    question: str,
    *,
    lexicon: Sequence[BilingualLexiconEntry] = DEFAULT_CIDER_LEXICON,
    max_variants: int = 6,
    include_structured_expansion: bool = False,
) -> list[QueryVariant]:
    if not 1 <= max_variants <= 6:
        raise ValueError("deep-research variants must be bounded between one and six")
    normalized = " ".join(question.split())
    if not 2 <= len(normalized) <= 2000:
        raise ValueError("deep-research question must contain between 2 and 2000 characters")
    anchors = _scientific_anchors(normalized)
    variants = [
        QueryVariant(
            text=normalized,
            language="mixed",
            derivation="original",
            anchor_terms=anchors,
        )
    ]

    # A barrel-ageing question about Calvados needs evidence along separate
    # scientific axes, not a single generic "oak + aroma" query.  Keep this
    # controlled expansion narrow: the general bilingual/occurrence behaviour
    # below remains unchanged for the established cider QA cases.
    intent = analyze_scientific_intent(normalized)
    if (
        include_structured_expansion
        and intent.matrix_primary
        and intent.process_terms
        and intent.facets
    ):
        for facet_key, text, scope_tier in intent_query_variants(intent, max_variants=max_variants):
            if facet_key == "overall":
                continue
            extra_terms = _FACET_QUERY_SYNONYMS.get(facet_key, ())
            variants.append(
                QueryVariant(
                    text=" ".join(dict.fromkeys([text, *extra_terms])),
                    language="en",
                    derivation="matched_terms",
                    matched_terms=[f"scientific_intent:{facet_key}"],
                    # Near-matrix apple/cider brandy papers often do not use
                    # the protected designation "Calvados" in their text.
                    # Matrix/process terms in the variant and the downstream
                    # causal reranker provide the controlled gate instead.
                    anchor_terms=[],
                    scope_tier=scope_tier,
                )
            )
    swapped = normalized
    matched: list[str] = []
    matched_fr: list[str] = []
    matched_en: list[str] = []
    for entry in lexicon:
        changed = False
        swapped, replaced_fr = _replace_phrase(swapped, entry.fr, entry.en)
        if replaced_fr:
            matched_fr.append(entry.fr)
            matched_en.append(entry.en)
            changed = True
        elif entry.en.casefold() != entry.fr.casefold():
            swapped, replaced_en = _replace_phrase(swapped, entry.en, entry.fr)
            if replaced_en:
                matched_fr.append(entry.fr)
                matched_en.append(entry.en)
                changed = True
        if changed:
            matched.append(f"{entry.fr} ↔ {entry.en}")
    if swapped != normalized:
        variants.append(
            QueryVariant(
                text=swapped,
                language="mixed",
                derivation="lexicon_swap",
                matched_terms=matched,
                anchor_terms=anchors,
            )
        )
    if matched_fr:
        targeted_fr = [*anchors, *dict.fromkeys(matched_fr)]
        targeted_en = [*anchors, *dict.fromkeys(matched_en)]
        occurrence_intent = _has_occurrence_intent(normalized)
        if occurrence_intent:
            targeted_fr.extend(_OCCURRENCE_TERMS_FR)
            targeted_en.extend(_OCCURRENCE_TERMS_EN)
        variants.extend(
            (
                QueryVariant(
                    text=" ".join(dict.fromkeys(targeted_fr)),
                    language="fr",
                    derivation="matched_terms",
                    matched_terms=matched,
                    anchor_terms=anchors,
                ),
                QueryVariant(
                    text=" ".join(dict.fromkeys(targeted_en)),
                    language="en",
                    derivation="matched_terms",
                    matched_terms=matched,
                    anchor_terms=anchors,
                ),
            )
        )
        if (
            anchors
            and occurrence_intent
            and _has_apple_juice_matrix(normalized)
            and _has_process_qualifier(normalized)
        ):
            variants.extend(
                (
                    QueryVariant(
                        text=" ".join(
                            dict.fromkeys([*anchors, "apple juice", *_OCCURRENCE_TERMS_EN])
                        ),
                        language="en",
                        derivation="matched_terms",
                        matched_terms=matched,
                        anchor_terms=anchors,
                        scope_tier="near_matrix",
                    ),
                    QueryVariant(
                        text=" ".join(
                            dict.fromkeys(
                                [
                                    *anchors,
                                    "apple juice",
                                    "model system",
                                    "inoculated",
                                    "growth",
                                    "survival",
                                ]
                            )
                        ),
                        language="en",
                        derivation="matched_terms",
                        matched_terms=matched,
                        anchor_terms=anchors,
                        scope_tier="model_matrix",
                    ),
                )
            )
    unique = {variant.text.casefold(): variant for variant in variants}
    return list(unique.values())[:max_variants]
