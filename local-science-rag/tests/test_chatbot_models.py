"""Contracts for persisted chatbot evidence and facet drafts."""

import pytest
from pydantic import ValidationError

from app.models.chatbot import ChatbotFacetDraft, ChatEvidencePassage, ChatEvidenceRecord


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
