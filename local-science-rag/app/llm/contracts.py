"""Provider-neutral contracts for bounded structured generation."""

from __future__ import annotations

from typing import Literal

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
