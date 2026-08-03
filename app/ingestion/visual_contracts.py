"""Provider-neutral contracts for current captions and future image inference."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VisualContextCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: int = Field(ge=0)
    column: int = Field(ge=0)
    text: str = Field(max_length=500)


class VisualCaptionContext(BaseModel):
    """Bounded source context that may be used without reading image pixels."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["table", "figure"]
    original_caption: str | None = Field(default=None, max_length=4_000)
    cells: list[VisualContextCell] = Field(default_factory=list, max_length=100)
    related_source_excerpts: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_context(self) -> VisualCaptionContext:
        if self.kind == "figure" and self.cells:
            raise ValueError("figure caption context cannot contain table cells")
        if any(len(excerpt) > 1_000 for excerpt in self.related_source_excerpts):
            raise ValueError("related source excerpts are limited to 1000 characters")
        return self


class ContextCaptionRequest(BaseModel):
    """Versioned input for the existing context-only synthetic caption workflow."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    article_id: str = Field(min_length=1, max_length=200)
    element_id: str = Field(min_length=1, max_length=500)
    page_number: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    context: VisualCaptionContext

    @model_validator(mode="after")
    def validate_bbox(self) -> ContextCaptionRequest:
        x0, y0, x1, y1 = self.bbox
        if x1 <= x0 or y1 <= y0:
            raise ValueError("visual element bounding box is invalid")
        return self


class SyntheticCaptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caption: str = Field(min_length=1, max_length=4_000)


class ContextCaptionGateway(Protocol):
    """Provider boundary for the current text-context caption generation."""

    def caption(self, request: ContextCaptionRequest) -> SyntheticCaptionResponse: ...


class VisualArtifactDescriptor(BaseModel):
    """Immutable, path-free identity for pixels that may cross a process boundary."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    asset_id: UUID
    source_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]
    byte_size: int = Field(gt=0, le=25_000_000)
    pixel_width: int = Field(gt=0, le=20_000)
    pixel_height: int = Field(gt=0, le=20_000)

    @model_validator(mode="after")
    def validate_bbox(self) -> VisualArtifactDescriptor:
        x0, y0, x1, y1 = self.bbox
        if x1 <= x0 or y1 <= y0:
            raise ValueError("visual artifact bounding box is invalid")
        return self


class ImageCaptionRequest(BaseModel):
    """Future server-safe request metadata; image bytes travel separately."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    artifact: VisualArtifactDescriptor
    context: VisualCaptionContext
    prompt_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    model_profile: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")

    @property
    def idempotency_key(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ImageCaptionResponse(BaseModel):
    """Traceable non-citable result returned by a visual inference provider."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    caption: str = Field(min_length=1, max_length=4_000)
    model_id: str = Field(min_length=1, max_length=200)
    model_revision: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class ImageCaptionGateway(Protocol):
    """Boundary implementable by a local model or a remote GPU service."""

    def caption(
        self,
        request: ImageCaptionRequest,
        *,
        image: bytes,
    ) -> ImageCaptionResponse: ...


class ScientificFigureAnalysisRequest(BaseModel):
    """Versioned, path-free input for question-targeted figure understanding."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    artifact: VisualArtifactDescriptor
    context: VisualCaptionContext
    question: str = Field(min_length=2, max_length=4_000)
    prompt_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    model_profile: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")

    @property
    def idempotency_key(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ScientificFigureAnalysisResponse(BaseModel):
    """Candidate visual observation returned by a replaceable inference provider."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    figure_type: Literal["graph", "diagram", "photo", "map", "microscopy", "other"]
    relevance_score: float = Field(ge=0.0, le=1.0)
    readability_score: float = Field(ge=0.0, le=1.0)
    supports_answer: bool
    summary: str = Field(min_length=1, max_length=1_200)
    visible_variables: list[str] = Field(default_factory=list, max_length=12)
    visible_units: list[str] = Field(default_factory=list, max_length=12)
    trends: list[str] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=6)
    model_id: str = Field(min_length=1, max_length=200)
    model_revision: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    warnings: list[str] = Field(default_factory=list, max_length=20)


class VisualModelIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=200)
    model_revision: str = Field(min_length=1, max_length=200)


class ScientificFigureAnalysisGateway(Protocol):
    """Provider boundary for local Ollama or a future isolated GPU service."""

    def identity(self) -> VisualModelIdentity: ...

    def analyze(
        self,
        request: ScientificFigureAnalysisRequest,
        *,
        image: bytes,
    ) -> ScientificFigureAnalysisResponse: ...
