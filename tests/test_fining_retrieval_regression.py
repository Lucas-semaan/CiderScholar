from __future__ import annotations

from app.retrieval.scientific_intent import analyze_scientific_intent, score_scientific_text

QUESTION = (
    "Quel est l'intérêt du collage dans les jus de pomme ? Donner des éléments "
    "de comparaison entre les colles végétales et animales."
)


def test_fining_intent_separates_process_effects_and_agent_comparison() -> None:
    intent = analyze_scientific_intent(QUESTION)

    assert intent.matrix_primary == ["jus de pomme", "apple juice"]
    assert "fining" in intent.process_terms
    assert {facet.key for facet in intent.facets} == {
        "fining_effects",
        "fining_agents_comparison",
        "fining_mechanisms_conditions",
    }
    mechanisms = intent.facet("fining_mechanisms_conditions")
    assert mechanisms is not None
    assert "bentonite" in mechanisms.terms
    assert "minéraux" in mechanisms.label.casefold()
    assert "cashew apple juice" in intent.excluded_terms


def test_deep_fining_comparison_covers_effects_mechanisms_comparison_and_limits() -> None:
    intent = analyze_scientific_intent(QUESTION, deep=True)

    assert [facet.key for facet in intent.facets] == [
        "fining_effects",
        "fining_agents_comparison",
        "fining_mechanisms_conditions",
        "fining_tradeoffs_limits",
    ]


def test_balanced_fining_comparison_keeps_mineral_agents_without_deep_limits() -> None:
    intent = analyze_scientific_intent(QUESTION)

    assert [facet.key for facet in intent.facets] == [
        "fining_effects",
        "fining_agents_comparison",
        "fining_mechanisms_conditions",
    ]


def test_direct_apple_juice_bentonite_study_is_retained_as_mineral_fining_evidence() -> None:
    intent = analyze_scientific_intent(QUESTION)

    result = score_scientific_text(
        intent,
        title="The Impact of Bentonite Fining on Apple Juice Clarity and Sodium Content",
        text=(
            "Bentonite is a mineral clay used for apple juice fining. The treatment reduced "
            "turbidity by adsorbing positively charged proteins, while activated bentonites "
            "also changed sodium content."
        ),
    )

    assert result.evidence_grade == "A"
    assert "fining_mechanisms_conditions" in result.matched_facets


def test_fining_comparison_beats_other_apple_juice_clarification_processes() -> None:
    intent = analyze_scientific_intent(QUESTION)
    direct = score_scientific_text(
        intent,
        title="Plant proteins as apple juice fining agents compared with gelatin",
        text=(
            "Pea and potato proteins were compared with the animal protein gelatin for "
            "fining efficacy, turbidity, phenolic removal and sensory quality."
        ),
    )
    membrane = score_scientific_text(
        intent,
        title=(
            "Potential of cross-flow microfiltration on mineral membranes for cashew "
            "apple juice clarification"
        ),
        text="Membrane filtration reduced pulp and turbidity in clarified cashew apple juice.",
    )
    electroflotation = score_scientific_text(
        intent,
        title="Electroflotation clarification of apple juice",
        text=(
            "Classic juice clarification was improved when electroflotation was carried out "
            "with addition of a small amount of gelatin. Combining electroflotation with "
            "ultrafiltration reduced turbidity in clarified apple juice."
        ),
    )

    assert direct.evidence_grade == "A"
    assert membrane.evidence_grade == "D"
    assert membrane.excluded_concept_match is True
    assert electroflotation.evidence_grade == "C"
    assert direct.score > electroflotation.score


def test_persisted_wine_cider_protein_clarification_is_supportive_for_apple_juice() -> None:
    intent = analyze_scientific_intent(QUESTION)
    title = "EFFICIENCY EVALUATION OF USING VEGETABLE PROTEINS FOR WINE AND CIDER CLARIFICATION"
    text = (
        "Plant and vegetable proteins were evaluated for wine and cider clarification. "
        "Their clarification efficiency was compared with gelatin through turbidity and "
        "phenolic measurements."
    )

    result = score_scientific_text(intent, title=title, text=text)

    assert "fining" not in f"{title} {text}".casefold()
    assert result.matrix_tier == "near"
    assert result.process_score == 1.0
    assert result.evidence_grade == "B"
