"""Contracts for persisted chatbot evidence and facet drafts."""

import pytest
from pydantic import ValidationError

from app.models.chatbot import (
    ChatbotCitationAnchor,
    ChatbotCitationEvidence,
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


def test_full_text_passage_accepts_structural_locator_but_rejects_mixed_coordinates() -> None:
    passage = ChatEvidencePassage(
        evidence_id="native:1",
        chunk_id=1,
        text="Native XML evidence.",
        locator_kind="structural",
        section_path="Results/Fermentation",
        paragraph_start=2,
        paragraph_end=3,
        xml_id_start="p2",
        xml_id_end="p3",
    )

    assert passage.page_start is None
    assert passage.paragraph_end == 3
    with pytest.raises(ValidationError, match="cannot carry page"):
        ChatEvidencePassage(
            evidence_id="invalid-native:1",
            chunk_id=1,
            text="Native XML evidence.",
            locator_kind="structural",
            section_path="Results",
            paragraph_start=2,
            paragraph_end=2,
            page_start=1,
            page_end=1,
        )


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


def test_citation_anchor_requires_unique_exact_evidence() -> None:
    evidence = ChatbotCitationEvidence(
        evidence_id="record-1:chunk:2",
        snippet="A persisted result.",
        chunk_id=2,
        page_start=4,
        page_end=4,
    )
    anchor = ChatbotCitationAnchor(
        citation_id="cite-0123456789abcdef",
        display_index=1,
        label="(Test, 2025, p. 4)",
        record_id="record-1",
        source_family="scientific_publication",
        article_id="article-1",
        title="A relevant article",
        evidence=[evidence],
    )

    assert anchor.evidence[0].page_start == 4
    with pytest.raises(ValidationError, match="must be unique"):
        ChatbotCitationAnchor(
            citation_id="cite-0123456789abcdef",
            display_index=1,
            label="(Test, 2025, p. 4)",
            record_id="record-1",
            source_family="scientific_publication",
            article_id="article-1",
            title="A relevant article",
            evidence=[evidence, evidence],
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
        selected_full_text_article_count=4,
        selected_full_text_passage_count=16,
        selected_abstract_article_count=2,
        selected_abstract_passage_count=2,
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
    assert trace.selected_full_text_article_count == 4
    assert trace.selected_abstract_article_count == 2

    with pytest.raises(ValidationError):
        ChatbotRetrievalTrace(
            stage="semantic_filter",
            rejection_counts={"free-form reason": 1},
        )

    with pytest.raises(ValidationError):
        ChatbotRetrievalTrace(
            stage="llm_context",
            selected_article_count=1,
            selected_full_text_article_count=1,
            selected_abstract_article_count=1,
        )
