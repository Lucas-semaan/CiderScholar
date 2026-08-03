from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.ingestion.visual_contracts import (
    ImageCaptionRequest,
    VisualArtifactDescriptor,
    VisualCaptionContext,
    VisualContextCell,
)


def _artifact() -> VisualArtifactDescriptor:
    return VisualArtifactDescriptor(
        asset_id=uuid4(),
        source_document_sha256="a" * 64,
        image_sha256="b" * 64,
        page_number=4,
        bbox=(10.0, 20.0, 300.0, 200.0),
        mime_type="image/png",
        byte_size=12_345,
        pixel_width=1_160,
        pixel_height=720,
    )


def test_visual_artifact_descriptor_is_path_free_and_strict() -> None:
    descriptor = _artifact()

    assert "path" not in descriptor.model_dump(mode="json")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VisualArtifactDescriptor.model_validate(
            {
                **descriptor.model_dump(mode="json"),
                "local_path": "C:/private/article.pdf",
            }
        )


def test_image_caption_request_has_stable_versioned_idempotency_key() -> None:
    descriptor = _artifact()
    context = VisualCaptionContext(
        kind="figure",
        original_caption="Figure 1. Mesures.",
        related_source_excerpts=["Le profil mesuré est présenté dans la figure."],
    )

    first = ImageCaptionRequest(
        artifact=descriptor,
        context=context,
        prompt_version="figure-caption-v1",
        model_profile="visual-default",
    )
    second = ImageCaptionRequest.model_validate(first.model_dump(mode="json"))
    changed = first.model_copy(update={"prompt_version": "figure-caption-v2"})

    assert first.idempotency_key == second.idempotency_key
    assert first.idempotency_key != changed.idempotency_key


def test_figure_context_rejects_table_cells() -> None:
    with pytest.raises(ValidationError, match="figure caption context"):
        VisualCaptionContext(
            kind="figure",
            cells=[VisualContextCell(row=0, column=0, text="12.4")],
        )
