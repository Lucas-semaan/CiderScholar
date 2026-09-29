"""Shared boundary checks for the two ARGO retrieval planners."""

from __future__ import annotations

import json

import pytest

from app.retrieval.generated_json import parse_generated_json


def test_generated_json_normalizes_nested_text_in_whole_document_fence() -> None:
    payload = '```json\n{"question": "cider\\u200b  yield", "queries": ["ＡＢＣ\\n  cider"]}\n```'

    assert parse_generated_json(payload) == {
        "question": "cider yield",
        "queries": ["ABC cider"],
    }


def test_generated_json_rejects_text_outside_document_fence() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_generated_json('Explanation:\n```json\n{"question": "cider"}\n```')
