from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.corpora import CorpusScope, corpus_paths
from app.database.sqlite import Database
from app.deep_research.admission import (
    ClaimAdmissionCheckpoint,
    ClaimAdmissionDecision,
)
from app.deep_research.claims import AtomicClaim, AtomicClaimCheckpoint, AtomicClaimEvidence
from app.deep_research.rendering import SQLiteDeepResearchRenderer
from app.ingestion.visual_enrichment import SyntheticCaptionEnricher
from app.jobs.contracts import DeepResearchPayload
from app.retrieval.lexical_search import LexicalSearchService


class _Client:
    def chat(self, _messages, **_kwargs):
        return SimpleNamespace(
            content=json.dumps({"caption": "Graphique des variations de l’acide malique."})
        )


def _seed(settings) -> tuple[Database, int, str]:
    database = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
    database.initialize()
    source_text = "Les valeurs mesurées sont présentées dans la figure liée."
    database.save_article_and_chunks(
        {
            "id": "visual-article",
            "sha256": "a" * 64,
            "doi": "10.1234/visual",
            "title": "Article visuel",
            "authors": ["Ada Visuelle"],
            "pdf_path": "data/visual.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": 4,
                "page_end": 4,
                "chunk_index": 0,
                "text": source_text,
                "token_count": 9,
            }
        ],
        [
            {
                "element_id": "figure-p0004-001",
                "kind": "figure",
                "page_number": 4,
                "bbox": (10.0, 20.0, 300.0, 200.0),
                "source_kind": "pdf_embedded",
                "source_locator": "image-xref:7",
                "original_caption": "Figure 1. Variations mesurées.",
                "synthetic_caption": None,
                "cells": [],
                "text_relations": [
                    {
                        "relation": "nearest_page_text",
                        "page_number": 4,
                        "source_excerpt": source_text,
                    }
                ],
            }
        ],
    )
    return database, database.article_chunk_ids("visual-article")[0], source_text


def test_synthetic_caption_improves_search_but_returns_only_source_chunk(settings) -> None:
    database, _chunk_id, source_text = _seed(settings)
    original = database.document_elements("visual-article")[0]["original_caption"]

    count = SyntheticCaptionEnricher(database, _Client()).enrich_article("visual-article")
    stored = database.document_elements("visual-article")[0]
    response = LexicalSearchService(settings, database).search("acide malique")

    assert count == 1
    assert stored["original_caption"] == original
    assert stored["synthetic_caption"] == "Graphique des variations de l’acide malique."
    assert len(response.results) == 1
    assert response.results[0].article_id == "visual-article"
    assert response.results[0].text == source_text
    assert "malique" not in response.results[0].text.casefold()


def test_synthetic_caption_cannot_be_used_as_verbatim_citation(settings) -> None:
    database, chunk_id, source_text = _seed(settings)
    synthetic = "Graphique des variations de l’acide malique."
    element_id = database.document_elements("visual-article")[0]["id"]
    database.set_synthetic_document_caption(element_id, synthetic)
    claim = AtomicClaim(
        claim_id="claim-" + "b" * 20,
        statement="L’acide malique varie.",
        role="result",
        evidence=[
            AtomicClaimEvidence(
                scope="common",
                article_id="visual-article",
                chunk_id=chunk_id,
                page_start=4,
                page_end=4,
                source_excerpt=synthetic,
                source_text_sha256=hashlib.sha256(source_text.encode()).hexdigest(),
            )
        ],
    )
    claims = AtomicClaimCheckpoint(
        question_sha256="c" * 64,
        source_fragment_count=1,
        claims=[claim],
    )
    admission = ClaimAdmissionCheckpoint(
        decisions=[
            ClaimAdmissionDecision(
                claim_id=claim.claim_id,
                status="accepted",
                reason="semantically_supported",
                admitted_statement=claim.statement,
            )
        ]
    )
    renderer = SQLiteDeepResearchRenderer(
        settings,
        settings.paths.cache_dir / "deep_research",
    )
    payload = DeepResearchPayload(
        message="Que montre la figure ?",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )

    with pytest.raises(RuntimeError, match="source changed"):
        renderer.render(payload, claims, admission)
