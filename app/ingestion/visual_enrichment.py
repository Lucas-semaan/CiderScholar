"""Optional synthetic captions stored only as non-citable retrieval enrichment."""

from __future__ import annotations

import json
from typing import Any, Protocol

from app.database.sqlite import Database
from app.ingestion.visual_contracts import (
    ContextCaptionGateway,
    ContextCaptionRequest,
    SyntheticCaptionResponse,
    VisualCaptionContext,
    VisualContextCell,
)


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


class ArgoContextCaptionGateway:
    """Adapt the current text-only ARGO client to the provider-neutral boundary."""

    def __init__(self, client: VisualCaptionClient) -> None:
        self.client = client

    def caption(self, request: ContextCaptionRequest) -> SyntheticCaptionResponse:
        response = self.client.chat(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        request.context.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                },
            ],
            json_schema=_CAPTION_SCHEMA,
            temperature=0.0,
            max_output_tokens=512,
        )
        return SyntheticCaptionResponse.model_validate_json(response.content)


class SyntheticCaptionEnricher:
    def __init__(
        self,
        database: Database,
        gateway: ContextCaptionGateway,
        *,
        max_elements: int = 20,
    ) -> None:
        if not 1 <= max_elements <= 100:
            raise ValueError("visual caption enrichment limit must be between 1 and 100")
        self.database = database
        self.gateway = gateway
        self.max_elements = max_elements

    def enrich_article(self, article_id: str) -> int:
        elements = self.database.document_elements(article_id)[: self.max_elements]
        enriched = 0
        for element in elements:
            if element["synthetic_caption"] is not None:
                continue
            request = ContextCaptionRequest(
                article_id=article_id,
                element_id=str(element["id"]),
                page_number=int(element["page_number"]),
                bbox=tuple(float(value) for value in element["bbox"]),
                context=VisualCaptionContext(
                    kind=element["kind"],
                    original_caption=element["original_caption"],
                    cells=[
                        VisualContextCell(
                            row=cell["row_index"],
                            column=cell["column_index"],
                            text=str(cell["text"])[:500],
                        )
                        for cell in element["cells"][:100]
                    ],
                    related_source_excerpts=[
                        str(relation["source_excerpt"])[:1000]
                        for relation in element["text_relations"][:4]
                    ],
                ),
            )
            generated = self.gateway.caption(request)
            self.database.set_synthetic_document_caption(
                str(element["id"]),
                generated.caption,
            )
            enriched += 1
        return enriched
