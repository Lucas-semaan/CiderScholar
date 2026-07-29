from __future__ import annotations

import pytest

from app.llm.response_style import ResponseStyle, detect_response_style


def test_response_style_defaults_to_prose_without_format_instruction() -> None:
    assert (
        detect_response_style("Quels facteurs influencent la fermentation ?") is ResponseStyle.PROSE
    )


@pytest.mark.parametrize("question", ["", "Présente les résultats.", "Use a table."])
def test_response_style_falls_back_to_prose_for_unknown_input(question: str) -> None:
    assert detect_response_style(question) is ResponseStyle.PROSE


def test_response_style_detects_explicit_french_list_request() -> None:
    assert detect_response_style("Liste les facteurs importants.") is ResponseStyle.BULLET_LIST


def test_response_style_detects_explicit_english_list_request() -> None:
    assert detect_response_style("List the important factors.") is ResponseStyle.BULLET_LIST


def test_response_style_gives_explicit_prohibition_priority() -> None:
    assert detect_response_style("Liste les facteurs, mais sans puces.") is ResponseStyle.PROSE


@pytest.mark.parametrize("value", ["paragraphs", "table", "json"])
def test_response_style_rejects_unknown_values(value: str) -> None:
    with pytest.raises(ValueError):
        ResponseStyle(value)
