from __future__ import annotations

from app.deep_research.query_variants import build_bilingual_variants
from app.retrieval.query_planning import deterministic_query_plan
from app.retrieval.scientific_intent import (
    analyze_scientific_intent,
    intent_query_variants,
    score_scientific_text,
)

QUESTION = "Quel est l’impact du cuvage pour les jus de pomme et les cidres ?"


def test_cuvage_is_disambiguated_as_pre_press_apple_mash_maceration() -> None:
    intent = analyze_scientific_intent(QUESTION, deep=True)

    assert {"jus de pomme", "apple juice", "cidre", "cider"} <= set(intent.matrix_primary)
    assert "mash maceration" in intent.process_terms_en
    assert "pre-press maceration" in intent.process_terms_en
    assert "maceration avant pressurage" in intent.process_terms_fr
    assert "carbonic maceration" in intent.excluded_terms
    assert "wine maceration" in intent.excluded_terms


def test_broad_process_question_still_gets_an_exact_controlled_query() -> None:
    intent = analyze_scientific_intent(QUESTION)
    controlled = [
        query
        for facet_key, query, tier in intent_query_variants(intent)
        if facet_key == "overall" and tier == "strict" and query != QUESTION
    ]

    assert controlled
    assert "apple juice" in controlled[0]
    assert "cider" in controlled[0]
    assert "mash maceration" in controlled[0]
    assert "pre-press maceration" in controlled[0]

    plan = deterministic_query_plan(QUESTION, deep=True).plan
    assert plan.axes[0].search_queries[0] == controlled[0]


def test_cuvage_bilingual_fallback_is_available_without_an_argo_plan() -> None:
    variants = build_bilingual_variants(QUESTION)
    structured = build_bilingual_variants(QUESTION, include_structured_expansion=True)

    assert any("mash maceration" in variant.text for variant in variants)
    assert any(
        variant.matched_terms == ["scientific_intent:process"]
        and "pre-press maceration" in variant.text
        for variant in structured
    )


def test_direct_mash_maceration_study_beats_process_false_friends() -> None:
    intent = analyze_scientific_intent(QUESTION)
    direct = score_scientific_text(
        intent,
        title=(
            "Effect of Mash Maceration on the Polyphenolic Content and Visual Quality "
            "Attributes of Cloudy Apple Juice"
        ),
        text=(
            "Apple mash maceration before pressing changed polyphenolic content and visual "
            "quality attributes."
        ),
    )
    storage = score_scientific_text(
        intent,
        title="Influence of Storage on Cloudy Apple Juice Quality",
        text="Cold storage changed colour and turbidity in apple juice.",
    )
    wine = score_scientific_text(
        intent,
        title="Carbonic Maceration of Grapes for Red Wine Production",
        text="Wine aroma was measured after alcoholic grape maceration.",
    )

    assert direct.evidence_grade == "A"
    assert direct.score > storage.score
    assert storage.evidence_grade == "C"
    assert wine.evidence_grade == "D"
    assert wine.excluded_concept_match is True
