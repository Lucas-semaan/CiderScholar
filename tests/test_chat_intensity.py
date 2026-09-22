"""Public answer effort changes budgets without weakening scientific safeguards."""

from uuid import uuid4

import pytest

from app.chat_effort import (
    AnswerEffort,
    answer_effort_budget,
    migrate_legacy_answer_effort,
)
from app.jobs.contracts import ChatAnswerPayload


def test_answer_effort_budgets_are_monotonic_and_bounded() -> None:
    concise = answer_effort_budget(AnswerEffort.CONCISE)
    balanced = answer_effort_budget(AnswerEffort.BALANCED)
    deep = answer_effort_budget(AnswerEffort.DEEP)

    for field in (
        "abstract_result_limit",
        "article_count",
        "passages_per_article",
        "candidate_chunks_per_article",
        "evidence_record_limit",
        "max_evidence_items",
        "max_evidence_characters",
        "mono_max_statements",
        "mono_max_output_tokens",
        "facet_max_statements",
        "final_max_output_tokens",
    ):
        assert getattr(concise, field) <= getattr(balanced, field) <= getattr(deep, field)

    assert concise.follow_up_incomplete_axes is False
    assert balanced.follow_up_incomplete_axes is False
    assert deep.follow_up_incomplete_axes is False
    assert {
        concise.max_retrieval_waves,
        balanced.max_retrieval_waves,
        deep.max_retrieval_waves,
    } == {1}
    assert (concise.verification_need_limit, balanced.verification_need_limit) == (3, 5)
    assert deep.verification_need_limit == 8
    assert (balanced.evidence_record_limit, balanced.max_evidence_items) == (16, 20)
    assert balanced.max_evidence_characters == 36_000
    assert balanced.target_duration_seconds == 15 * 60
    assert concise.target_duration_seconds is None
    assert deep.target_duration_seconds is None
    assert deep.article_count >= 16
    assert deep.evidence_record_limit >= 32
    assert deep.max_evidence_items >= 32
    assert concise.max_vector_query_variants == 2
    assert balanced.max_vector_query_variants == 2
    assert deep.max_vector_query_variants == 2
    assert deep.max_retrieval_waves == 1
    assert deep.mono_max_output_tokens == 8_192
    assert deep.facet_max_output_tokens == 3_072
    assert deep.final_max_output_tokens == 4_096
    assert deep.max_vector_query_variants < deep.max_query_variants


def test_chat_answer_payload_defaults_to_balanced_and_accepts_deep() -> None:
    identifiers = {"conversation_id": uuid4(), "client_request_id": uuid4()}
    assert (
        ChatAnswerPayload(message="Question", **identifiers).answer_effort is AnswerEffort.BALANCED
    )
    payload = ChatAnswerPayload(message="Question", answer_effort="deep", **identifiers)
    assert payload.answer_effort is AnswerEffort.DEEP


def test_chat_answer_payload_migrates_persisted_legacy_intensity() -> None:
    payload = ChatAnswerPayload(
        message="Question",
        answer_intensity="deep",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )
    assert payload.answer_effort is AnswerEffort.DEEP
    assert "answer_intensity" not in payload.model_dump(mode="json")


def test_legacy_answer_effort_migration_is_shared_and_non_mutating() -> None:
    legacy = {"answer_intensity": "deep", "message": "Question"}

    assert migrate_legacy_answer_effort(legacy) == {
        "answer_effort": "deep",
        "message": "Question",
    }
    assert legacy == {"answer_intensity": "deep", "message": "Question"}

    current = {"answer_effort": "balanced"}
    assert migrate_legacy_answer_effort(current) is current
    with pytest.raises(ValueError, match="cannot be supplied together"):
        migrate_legacy_answer_effort({"answer_effort": "balanced", "answer_intensity": "deep"})
