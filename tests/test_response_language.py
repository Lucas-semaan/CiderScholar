from __future__ import annotations

import pytest

from app.llm.response_language import question_language, validate_output_language


def test_question_language_uses_current_question_only() -> None:
    assert question_language("Quels effets le vieillissement produit-il ?") == "fr"
    assert question_language("What effects does wood aging produce?") == "en"


def test_each_generated_element_must_match_the_question_language() -> None:
    with pytest.raises(RuntimeError, match="output element 2"):
        validate_output_language(
            "Quels effets le vieillissement produit-il ?",
            [
                "Le vieillissement modifie les arômes.",
                "The study shows aging effects.",
                "Les preuves disponibles restent limitées.",
            ],
        )


def test_common_latin_scientific_phrases_do_not_count_as_english_prose() -> None:
    validate_output_language(
        "Quel effet est observé ?",
        ["L'effet observé in vitro reste limité et doit être confirmé in vivo."],
    )
