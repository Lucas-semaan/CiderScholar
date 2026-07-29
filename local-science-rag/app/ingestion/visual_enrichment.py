"""Optional synthetic captions stored only as non-citable retrieval enrichment."""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.database.sqlite import Database


class SyntheticCaptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caption: str = Field(min_length=1, max_length=4_000)


class VisualCaptionClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Any: ...


_CAPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"caption": {"type": "string", "maxLength": 4000}},
    "required": ["caption"],
    "additionalProperties": False,
}
_SYSTEM_PROMPT = (
    "Produis une courte légende synthétique destinée uniquement à améliorer la recherche locale. "
    "Utilise seulement la légende originale, les cellules et les extraits liés fournis. N'invente "
    "aucun résultat, DOI, auteur, unité ou page. Cette légende ne sera jamais une preuve. Réponds "
    "seulement avec l'objet JSON demandé."
)


class SyntheticCaptionEnricher:
    def __init__(
        self,
        database: Database,
        client: VisualCaptionClient,
        *,
        max_elements: int = 20,
    ) -> None:
        if not 1 <= max_elements <= 100:
            raise ValueError("visual caption enrichment limit must be between 1 and 100")
        self.database = database
        self.client = client
        self.max_elements = max_elements

    def enrich_article(self, article_id: str) -> int:
        elements = self.database.document_elements(article_id)[: self.max_elements]
        enriched = 0
        for element in elements:
            if element["synthetic_caption"] is not None:
                continue
            response = self.client.chat(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "kind": element["kind"],
                                "original_caption": element["original_caption"],
                                "cells": [
                                    {
                                        "row": cell["row_index"],
                                        "column": cell["column_index"],
                                        "text": str(cell["text"])[:500],
                                    }
                                    for cell in element["cells"][:100]
                                ],
                                "related_source_excerpts": [
                                    str(relation["source_excerpt"])[:1000]
                                    for relation in element["text_relations"][:4]
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                json_schema=_CAPTION_SCHEMA,
                temperature=0.0,
                max_output_tokens=512,
            )
            generated = SyntheticCaptionResponse.model_validate_json(response.content)
            self.database.set_synthetic_document_caption(
                str(element["id"]),
                generated.caption,
            )
            enriched += 1
        return enriched
