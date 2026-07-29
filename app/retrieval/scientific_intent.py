"""Deterministic scientific intent decomposition and causal relevance scoring."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def _plain_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(
        re.findall(
            r"[a-z0-9]+",
            "".join(character for character in normalized if not unicodedata.combining(character)),
        )
    )


def _contains_any(value: str, terms: Iterable[str]) -> bool:
    haystack = f" {_plain_text(value)} "
    return any(f" {_plain_text(term)} " in haystack for term in terms if _plain_text(term))


class ScientificFacet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    label: str = Field(min_length=2, max_length=120)
    terms_fr: list[str] = Field(min_length=1, max_length=24)
    terms_en: list[str] = Field(min_length=1, max_length=24)

    @property
    def terms(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys([*self.terms_fr, *self.terms_en]))


class ScientificIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=2, max_length=4000)
    matrix_primary: list[str] = Field(default_factory=list, max_length=12)
    matrix_close: list[str] = Field(default_factory=list, max_length=20)
    matrix_distant: list[str] = Field(default_factory=list, max_length=20)
    process_terms_fr: list[str] = Field(default_factory=list, max_length=24)
    process_terms_en: list[str] = Field(default_factory=list, max_length=24)
    facets: list[ScientificFacet] = Field(default_factory=list, max_length=6)

    @property
    def process_terms(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys([*self.process_terms_fr, *self.process_terms_en]))

    @property
    def is_structured(self) -> bool:
        return bool(self.matrix_primary or self.process_terms or self.facets)

    def facet(self, key: str) -> ScientificFacet | None:
        return next((facet for facet in self.facets if facet.key == key), None)

    def central_concepts(self) -> list[str]:
        values = [
            *self.matrix_primary,
            *self.matrix_close[:3],
            *self.process_terms_en[:3],
            *(facet.terms_en[0] for facet in self.facets),
        ]
        return list(dict.fromkeys(value for value in values if value))[:12]

    def selector_query(self) -> str:
        concepts = [
            self.question,
            *self.matrix_close[:4],
            *self.process_terms_en[:5],
            *(term for facet in self.facets for term in facet.terms_en[:5]),
        ]
        return " ".join(dict.fromkeys(concepts))[:4000]


class ScientificRelevance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    matrix_score: float = Field(ge=0.0, le=1.0)
    process_score: float = Field(ge=0.0, le=1.0)
    outcome_score: float = Field(ge=0.0, le=1.0)
    matrix_tier: Literal["exact", "near", "distant", "none"]
    matched_facets: list[str]
    causal_match: bool


CALVADOS_PRIMARY = ("calvados",)
CALVADOS_CLOSE = (
    "cider brandy",
    "cider brandies",
    "apple brandy",
    "apple brandies",
    "apple spirit",
    "apple spirits",
    "cider spirit",
    "cider spirits",
    "cider distillate",
    "cider distillates",
    "eau de vie de pomme",
    "eau de vie de cidre",
    "eau-de-vie de pomme",
    "eau-de-vie de cidre",
)
CALVADOS_DISTANT = (
    "fruit brandy",
    "fruit spirit",
    "grape spirit",
    "grape spirits",
    "pear spirit",
    "pear spirits",
    "apple and pear spirit",
    "apple and pear spirits",
    "brandy",
    "cognac",
    "armagnac",
    "whisky",
    "wine",
    "vin",
    "apple cider",
    "cider",
    "ciders",
)

WOOD_PROCESS_FR = (
    "elevage en barrique",
    "vieillissement en fut",
    "vieillissement en barrique",
    "maturation sous bois",
    "maturation en fut",
    "barrique",
    "fut",
    "bois",
    "chene",
    "chauffe",
    "toastage",
)
WOOD_PROCESS_EN = (
    "barrel ageing",
    "barrel aging",
    "oak ageing",
    "oak aging",
    "wood maturation",
    "cask maturation",
    "wood ageing",
    "wood aging",
    "oak",
    "wood",
    "barrel",
    "cask",
    "toasted",
    "toast level",
    "wood chips",
)

AROMA_FACET = ScientificFacet(
    key="aroma",
    label="Arômes et composés volatils",
    terms_fr=[
        "arome",
        "aromes",
        "compose volatil",
        "composes volatils",
        "ester",
        "esters",
        "furfural",
        "lactone",
        "aldehyde",
        "profil sensoriel",
    ],
    terms_en=[
        "aroma",
        "flavour",
        "flavor",
        "volatile compounds",
        "volatile profile",
        "esters",
        "furfural",
        "oak lactone",
        "aldehydes",
        "sensory profile",
    ],
)
STRUCTURE_FACET = ScientificFacet(
    key="structure",
    label="Structure, équilibre et perception en bouche",
    terms_fr=[
        "structure",
        "polyphenol",
        "polyphenols",
        "tanin",
        "tanins",
        "acidite",
        "astringence",
        "couleur",
        "bouche",
        "extraction",
    ],
    terms_en=[
        "structure",
        "phenolic compounds",
        "polyphenols",
        "tannins",
        "total acidity",
        "volatile acidity",
        "astringency",
        "color",
        "colour",
        "mouthfeel",
        "wood extraction",
    ],
)
EVOLUTION_FACET = ScientificFacet(
    key="evolution",
    label="Évolution chimique pendant la maturation",
    terms_fr=[
        "evolution",
        "oxydation",
        "extraction du bois",
        "hydrolyse",
        "esterification",
        "degradation des esters",
        "temps de vieillissement",
    ],
    terms_en=[
        "evolution",
        "oxidation",
        "wood extraction",
        "hydrolysis",
        "esterification",
        "ester degradation",
        "aging time",
        "maturation time",
    ],
)


def analyze_scientific_intent(question: str) -> ScientificIntent:
    """Decompose frequent cider-science questions without an external model call."""

    cleaned = " ".join(question.split())
    if len(cleaned) < 2:
        raise ValueError("scientific intent question is too short")
    normalized = _plain_text(cleaned)

    primary: list[str] = []
    close: list[str] = []
    distant: list[str] = []
    if _contains_any(normalized, CALVADOS_PRIMARY):
        primary = list(CALVADOS_PRIMARY)
        close = list(CALVADOS_CLOSE)
        distant = list(CALVADOS_DISTANT)
    elif _contains_any(normalized, CALVADOS_CLOSE):
        primary = [term for term in CALVADOS_CLOSE if _contains_any(normalized, (term,))]
        close = [term for term in CALVADOS_CLOSE if term not in primary]
        distant = list(CALVADOS_DISTANT)
    elif _contains_any(normalized, ("cidre", "cider")):
        primary = ["cidre", "cider"]
        close = ["apple cider", "hard cider"]
        distant = ["apple juice", "apple wine", "wine"]
    elif _contains_any(normalized, ("jus de pomme", "apple juice")):
        primary = ["jus de pomme", "apple juice"]
        close = ["apple must", "apple concentrate"]
        distant = ["model apple juice", "model solution"]

    process_fr: list[str] = []
    process_en: list[str] = []
    if _contains_any(normalized, WOOD_PROCESS_FR) or _contains_any(normalized, WOOD_PROCESS_EN):
        process_fr = list(WOOD_PROCESS_FR)
        process_en = list(WOOD_PROCESS_EN)

    facets: list[ScientificFacet] = []
    if _contains_any(normalized, AROMA_FACET.terms):
        facets.append(AROMA_FACET)
    if _contains_any(normalized, STRUCTURE_FACET.terms):
        facets.append(STRUCTURE_FACET)
    if (
        process_fr
        and STRUCTURE_FACET not in facets
        and _contains_any(normalized, ("structure", "bouche", "equilibre", "mouthfeel"))
    ):
        facets.append(STRUCTURE_FACET)
    if process_fr and any(facet.key in {"aroma", "structure"} for facet in facets):
        facets.append(EVOLUTION_FACET)

    return ScientificIntent(
        question=cleaned,
        matrix_primary=primary,
        matrix_close=close,
        matrix_distant=distant,
        process_terms_fr=process_fr,
        process_terms_en=process_en,
        facets=list({facet.key: facet for facet in facets}.values()),
    )


def intent_query_variants(
    intent: ScientificIntent,
    *,
    max_variants: int = 6,
) -> list[tuple[str, str, Literal["strict", "near_matrix", "model_matrix"]]]:
    """Return facet-aware English searches ordered from exact to distant matrices."""

    if not 1 <= max_variants <= 6:
        raise ValueError("intent query variants must be bounded between one and six")
    variants: list[tuple[str, str, Literal["strict", "near_matrix", "model_matrix"]]] = [
        ("overall", intent.question, "strict")
    ]
    process = list(intent.process_terms_en[:7])
    exact_matrix = list(intent.matrix_primary[:2])
    near_matrix = list(intent.matrix_close[:5])

    for facet in intent.facets:
        matrix = [*exact_matrix, *near_matrix]
        if not matrix and not process:
            continue
        query = " ".join(dict.fromkeys([*matrix, *process, *facet.terms_en[:8]]))
        if query:
            variants.append((facet.key, query, "strict" if exact_matrix else "near_matrix"))

    for facet in intent.facets:
        if len(variants) >= max_variants or not near_matrix:
            break
        query = " ".join(dict.fromkeys([*near_matrix, *process, *facet.terms_en[:8]]))
        if query:
            variants.append((facet.key, query, "near_matrix"))

    if len(variants) < max_variants and intent.matrix_distant and intent.facets:
        facet = intent.facets[0]
        query = " ".join(dict.fromkeys([*intent.matrix_distant[:4], *process, *facet.terms_en[:8]]))
        variants.append((facet.key, query, "model_matrix"))

    unique: list[tuple[str, str, Literal["strict", "near_matrix", "model_matrix"]]] = []
    seen: set[str] = set()
    for facet_key, text, tier in variants:
        normalized = _plain_text(text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append((facet_key, text, tier))
    return unique[:max_variants]


def score_scientific_text(
    intent: ScientificIntent,
    *,
    title: str,
    text: str,
) -> ScientificRelevance:
    """Score matrix + mechanism + outcomes and penalize one-dimensional matches."""

    searchable = f"{title}\n{text}"
    # The matrix named in the title is authoritative. This prevents a paper on
    # wine or grape spirits from becoming an "exact" Calvados match merely
    # because Calvados appears once in its abstract, discussion, or references.
    title_has_primary = _contains_any(title, intent.matrix_primary)
    title_has_close = _contains_any(title, intent.matrix_close)
    title_has_distant = _contains_any(title, intent.matrix_distant)
    if title_has_primary:
        matrix_score = 1.0
        matrix_tier: Literal["exact", "near", "distant", "none"] = "exact"
    elif title_has_close:
        matrix_score = 0.88
        matrix_tier = "near"
    elif title_has_distant:
        matrix_score = 0.28
        matrix_tier = "distant"
    elif _contains_any(text, intent.matrix_primary):
        matrix_score = 1.0
        matrix_tier = "exact"
    elif _contains_any(text, intent.matrix_close):
        matrix_score = 0.88
        matrix_tier = "near"
    elif _contains_any(text, intent.matrix_distant):
        matrix_score = 0.28
        matrix_tier = "distant"
    else:
        matrix_score = 0.0
        matrix_tier = "none"

    if intent.process_terms:
        process_score = 1.0 if _contains_any(searchable, intent.process_terms) else 0.0
    else:
        process_score = 1.0

    matched_facets = [
        facet.key for facet in intent.facets if _contains_any(searchable, facet.terms)
    ]
    outcome_score = len(matched_facets) / len(intent.facets) if intent.facets else 1.0
    causal_match = (
        matrix_score >= 0.8 and process_score > 0 and (not intent.facets or outcome_score > 0)
    )
    title_matrix = float(title_has_primary or title_has_close or title_has_distant)
    title_process = float(not intent.process_terms or _contains_any(title, intent.process_terms))
    title_outcomes = (
        sum(_contains_any(title, facet.terms) for facet in intent.facets) / len(intent.facets)
        if intent.facets
        else 1.0
    )
    title_alignment = 0.45 * title_matrix + 0.35 * title_process + 0.20 * title_outcomes
    base_score = 0.45 * matrix_score + 0.30 * process_score + 0.25 * outcome_score
    # Central title matches beat incidental mentions in references or
    # background paragraphs.
    score = base_score * (0.82 + 0.18 * title_alignment)
    if causal_match:
        score += 0.02
    if intent.matrix_primary and matrix_score == 0:
        score *= 0.20
    elif matrix_tier == "distant":
        score *= 0.55
    if intent.process_terms and process_score == 0:
        score *= 0.35
    if intent.facets and outcome_score == 0:
        score *= 0.45

    return ScientificRelevance(
        score=min(max(score, 0.0), 1.0),
        matrix_score=matrix_score,
        process_score=process_score,
        outcome_score=outcome_score,
        matrix_tier=matrix_tier,
        matched_facets=matched_facets,
        causal_match=causal_match,
    )


def facet_query(intent: ScientificIntent, facet: ScientificFacet) -> str:
    """Build one explicit facet question for provisional grounded synthesis."""

    return (
        f"{intent.question} Axe à traiter : {facet.label}. "
        f"Concepts ciblés : {', '.join(facet.terms_fr[:6])}; "
        f"{', '.join(facet.terms_en[:6])}."
    )[:4000]
