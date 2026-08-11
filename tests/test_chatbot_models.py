"""Contracts for persisted chatbot evidence and facet drafts."""

import pytest
from pydantic import ValidationError

from app.models.chatbot import (
    ChatbotFacetDraft,
    ChatbotRetrievalTrace,
    ChatbotTiming,
    ChatEvidencePassage,
    ChatEvidenceRecord,
)


def _abstract_passage() -> ChatEvidencePassage:
    return ChatEvidencePassage(evidence_id="abstract:1", text="Relevant abstract evidence.")


def test_evidence_record_defaults_facet_ranking_metadata() -> None:
    record = ChatEvidenceRecord(
        record_id="record-1",
        origin="local_rag",
        evidence_level="abstract",
        title="A relevant article",
        passages=[_abstract_passage()],
    )

    assert record.matched_facets == []
    assert record.matrix_tier == "none"


def test_facet_draft_is_bounded_and_serializable() -> None:
    draft = ChatbotFacetDraft(
        key="aroma",
        label="Arômes",
        query="Calvados oak ageing volatile compounds",
        answer_markdown="Les esters évoluent avec l'élevage.",
        cited_evidence_ids=["record-1:abstract:1"],
        source_record_ids=["record-1"],
    )

    assert draft.model_dump()["key"] == "aroma"

    with pytest.raises(ValidationError):
        ChatbotFacetDraft(
            key="x" * 101,
            label="Arômes",
            query="query",
            answer_markdown="answer",
        )


def test_observability_contracts_are_non_textual_and_boundary_labeled() -> None:
    trace = ChatbotRetrievalTrace(
        stage="full_text_reranking",
        query_variant_count=3,
        lexical_candidate_count=120,
        dense_candidate_count=120,
        rrf_unique_candidate_count=175,
        fused_candidate_count=100,
        pre_rerank_candidate_count=40,
        post_rerank_candidate_count=40,
        selected_article_count=6,
        selected_passage_count=18,
        rejection_counts={"not_selected_after_scientific_ranking": 34},
    )
    timing = ChatbotTiming(
        stage="full_text_search",
        duration_seconds=1.2,
        prompt_tokens=0,
        completion_tokens=0,
        process_rss_before_gb=1.0,
        process_rss_after_gb=1.2,
    )

    assert set(trace.model_dump()).isdisjoint(
        {"query", "article_id", "title", "doi", "text", "excerpt"}
    )
    assert timing.process_rss_before_gb == 1.0
    assert timing.process_rss_after_gb == 1.2

    with pytest.raises(ValidationError):
        ChatbotRetrievalTrace(
            stage="semantic_filter",
            rejection_counts={"free-form reason": 1},
        )
