from __future__ import annotations

import inspect

import pytest

from app.deep_research.query_variants import (
    build_bilingual_variants,
    query_variant_weight,
    variant_matches_text,
)


def test_bilingual_variants_are_bounded_and_inspectable() -> None:
    variants = build_bilingual_variants("Quel effet de l'oxygénation sur les polyphénol du cidre ?")

    assert 2 <= len(variants) <= 6
    assert variants[0].derivation == "original"
    assert any("oxygenation" in variant.text for variant in variants)
    assert any(variant.language == "en" for variant in variants)
    assert all(variant.matched_terms or variant.derivation == "original" for variant in variants)


def test_variant_builder_has_no_ciderqa_label_input() -> None:
    parameters = set(inspect.signature(build_bilingual_variants).parameters)

    assert parameters == {
        "question",
        "lexicon",
        "max_variants",
        "include_structured_expansion",
    }
    assert not parameters & {
        "expected_concepts",
        "expected_answer",
        "expected_article_ids",
        "reference_evidence",
    }


def test_variant_count_and_question_length_are_closed() -> None:
    with pytest.raises(ValueError, match="bounded"):
        build_bilingual_variants("Question", max_variants=7)
    with pytest.raises(ValueError, match="characters"):
        build_bilingual_variants(" ")


def test_occurrence_query_preserves_taxa_and_adds_controlled_matrix_fallbacks() -> None:
    variants = build_bilingual_variants(
        "Quelle est l’occurrence de Byssochlamys et Alicyclobacillus "
        "dans le jus de pomme pasteurisé ?"
    )

    assert len(variants) == 6
    assert all(variant.anchor_terms == ["Byssochlamys", "Alicyclobacillus"] for variant in variants)
    assert {variant.scope_tier for variant in variants} == {
        "strict",
        "near_matrix",
        "model_matrix",
    }
    assert any(
        variant.scope_tier == "near_matrix"
        and "apple juice" in variant.text
        and "prevalence" in variant.text
        and "pasteurized" not in variant.text
        for variant in variants
    )
    assert any(
        variant.scope_tier == "model_matrix" and "model system" in variant.text
        for variant in variants
    )
    assert (
        query_variant_weight(
            next(variant for variant in variants if variant.scope_tier == "strict")
        )
        > query_variant_weight(
            next(variant for variant in variants if variant.scope_tier == "near_matrix")
        )
        > query_variant_weight(
            next(variant for variant in variants if variant.scope_tier == "model_matrix")
        )
    )
    assert variant_matches_text(
        variants[0],
        "Byssochlamys fulva was isolated from the final apple juice.",
    )
    assert not variant_matches_text(
        variants[0],
        "Pasteurized apple juice colour and sensory quality were measured.",
    )


def test_calvados_barrel_ageing_query_is_not_systematically_split() -> None:
    variants = build_bilingual_variants(
        "impacte de l'élevage en barrique sur les aromes et la structure des calvados"
    )

    assert all(
        not variant.matched_terms or not variant.matched_terms[0].startswith("scientific_intent:")
        for variant in variants
    )

    variants = build_bilingual_variants(
        "impacte de l'élevage en barrique sur les aromes et la structure des calvados",
        include_structured_expansion=True,
    )
    assert len(variants) == 6
    assert variants[0].derivation == "original"
    intent_variants = [
        variant
        for variant in variants
        if variant.matched_terms and variant.matched_terms[0].startswith("scientific_intent:")
    ]
    assert {"scientific_intent:aroma", "scientific_intent:structure"} <= {
        variant.matched_terms[0] for variant in intent_variants
    }
    assert any(
        variant.scope_tier == "strict"
        and all(
            term in variant.text
            for term in ("calvados", "apple brandy", "cider brandy", "oak ageing")
        )
        for variant in intent_variants
    )
    structure_variants = [
        variant
        for variant in intent_variants
        if variant.matched_terms == ["scientific_intent:structure"]
    ]
    assert structure_variants
    assert all(
        "phenolics" in variant.text and "acidity" in variant.text for variant in structure_variants
    )
    assert any(variant.scope_tier == "near_matrix" for variant in intent_variants)
