from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict

from app.llm.structured_output import validate_structured_response


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str


def test_recovers_one_wrapped_schema_valid_object() -> None:
    content = f"<think>done</think>\n```json\n{json.dumps({'answer': 'ok'})}\n```"

    assert validate_structured_response(content, _Payload).answer == "ok"


def test_rejects_multiple_distinct_schema_valid_objects() -> None:
    content = '{"answer":"first"}\n{"answer":"second"}'

    with pytest.raises(ValueError, match="multiple schema-valid JSON objects"):
        validate_structured_response(content, _Payload)
