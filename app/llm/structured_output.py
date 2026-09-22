"""Strict recovery of one schema-valid JSON object from an LLM response."""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError


def validate_structured_response[ModelT: BaseModel](content: str, model: type[ModelT]) -> ModelT:
    """Validate an exact response, or recover one unambiguous valid JSON object.

    Some OpenAI-compatible providers ignore ``response_format`` and wrap an
    otherwise valid object in reasoning tags, prose, or Markdown. Recovery is
    deliberately conservative: every recovered object must pass the complete
    Pydantic schema, and more than one distinct valid object is rejected.
    """

    cleaned = content.strip().lstrip("\ufeff")
    lines = cleaned.splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().casefold() in {"```", "```json"}
        and lines[-1].strip() == "```"
    ):
        cleaned = "\n".join(lines[1:-1]).strip()
    try:
        return model.model_validate_json(cleaned)
    except ValidationError as strict_error:
        decoder = json.JSONDecoder()
        recovered: dict[str, ModelT] = {}
        for offset, character in enumerate(cleaned):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(cleaned, offset)
                payload = model.model_validate(value)
            except (json.JSONDecodeError, ValidationError):
                continue
            canonical = json.dumps(
                payload.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            recovered[canonical] = payload
        if len(recovered) == 1:
            return next(iter(recovered.values()))
        if len(recovered) > 1:
            raise ValueError("LLM response contains multiple schema-valid JSON objects") from (
                strict_error
            )
        raise
