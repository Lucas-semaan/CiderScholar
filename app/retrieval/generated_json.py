"""Decode untrusted planner JSON before its plan-specific Pydantic validation."""

from __future__ import annotations

import json
import unicodedata
from typing import Any


def _clean_values(value: Any) -> Any:
    if isinstance(value, str):
        # Invisible controls can change lexical matching while looking identical
        # in a review; normalize them before Pydantic accepts generated strings.
        normalized = unicodedata.normalize("NFKC", value)
        visible = "".join(
            character
            for character in normalized
            if unicodedata.category(character) not in {"Cc", "Cf"}
        )
        return " ".join(visible.split())
    if isinstance(value, list):
        return [_clean_values(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean_values(item) for key, item in value.items()}
    return value


def parse_generated_json(content: str) -> Any:
    """Accept plain JSON or one whole-document fence; preserve JSON errors for callers."""

    cleaned = content.strip()
    lines = cleaned.splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().casefold() in {"```", "```json"}
        and lines[-1].strip() == "```"
    ):
        cleaned = "\n".join(lines[1:-1]).strip()
    return _clean_values(json.loads(cleaned))
