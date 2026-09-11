"""Provider-neutral contracts for bounded structured generation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class GenerationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class GenerationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_duration_seconds: float = Field(ge=0.0)
    load_duration_seconds: float = Field(ge=0.0)
    prompt_eval_count: int = Field(ge=0)
    prompt_eval_duration_seconds: float = Field(ge=0.0)
    eval_count: int = Field(ge=0)
    eval_duration_seconds: float = Field(ge=0.0)


class GenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    content: str
    done_reason: str | None
    metrics: GenerationMetrics


class DictionaryGenerationClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Any: ...


class GenerationClient(Protocol):
    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        json_schema: Mapping[str, Any] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> GenerationResponse: ...


class ReservedGenerationClient(Protocol):
    def chat(
        self,
        messages: Sequence[GenerationMessage | Mapping[str, str]],
        *,
        json_schema: Mapping[str, Any] | None = None,
        max_output_tokens: int | None = None,
        on_request_reserved: Callable[[], None] | None = None,
    ) -> GenerationResponse: ...
