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
    assert balanced.follow_up_incomplete_axes is True
    assert deep.follow_up_incomplete_axes is True
    assert deep.article_count <= 10
    assert deep.evidence_record_limit <= 20
    assert deep.max_evidence_items <= 20


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
