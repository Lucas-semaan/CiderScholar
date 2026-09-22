"""Citable figure evidence is limited to source captions and neighbouring source text."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FigureEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    element_id: str = Field(min_length=1, max_length=256)
    article_id: str = Field(min_length=1, max_length=128)
    page_number: int = Field(ge=1)
    original_caption: str | None = Field(default=None, max_length=4_000)
    related_chunk_ids: tuple[int, ...] = Field(default=(), max_length=4)
    synthetic_caption: None = None


def figure_evidence_from_element(element: dict[str, object]) -> FigureEvidence:
    """Exclude any generated enrichment from source-derived figure evidence."""

    if element.get("kind") != "figure":
        raise ValueError("figure evidence requires a source figure element")
    relations = element.get("text_relations")
    related = (
        tuple(
            int(item["related_chunk_id"])
            for item in relations
            if isinstance(item, dict) and item.get("related_chunk_id") is not None
        )
        if isinstance(relations, list)
        else ()
    )
    return FigureEvidence(
        element_id=str(element["id"]),
        article_id=str(element["article_id"]),
        page_number=int(element["page_number"]),
        original_caption=(
            str(element["original_caption"]) if element.get("original_caption") else None
        ),
        related_chunk_ids=tuple(dict.fromkeys(related)),
    )
