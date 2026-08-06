"""Tests for DRS-009 (contextual summariser) and DRS-010 (relevance filter).

All tests use a fake ARGO client so no real network calls are made.
Private fragment text is used only in-process and never written to disk.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.corpora import CorpusScope
from app.deep_research.contextual_summary import (
    DEFAULT_SUMMARISER_TOP_K,
    ContextualSummarizer,
    ContextualSummaryResult,
    SummarisableFragment,
    build_contextual_evidence,
    filter_relevant,
)
from app.deep_research.models import ContextualEvidenceGate
from app.deep_research.pipeline import build_deep_research_operations
from app.deep_research.retrieval import DeepResearchFragmentHit
from app.llm.argo_client import ArgoClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fragment(
    chunk_id: int = 1,
    score: float = 0.9,
    scope: CorpusScope = CorpusScope.COMMON,
    text: str = "Fragment text about cider fermentation.",
    sha: str | None = None,
) -> SummarisableFragment:
    sha = sha or ("a" * 64)
    return SummarisableFragment(
        method="lexical",
        scope=scope,
        article_id="article-001",
        chunk_id=chunk_id,
        page_start=1,
        page_end=2,
        score=score,
        text_sha256=sha,
        text=text,
    )


def _make_argo_response(summary: str = "Good summary.", score: float = 0.8) -> MagicMock:
    """Return a mock that looks like ArgoClient.chat() returning valid JSON."""
    mock_response = MagicMock()
    mock_response.content = json.dumps({"summary": summary, "relevance_score": score})
    return mock_response


def _fake_client(response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.chat.return_value = response
    return client


# ---------------------------------------------------------------------------
# DRS-009 tests
# ---------------------------------------------------------------------------


def test_summariser_sends_only_bounded_fragments() -> None:
    """Only top_k=12 fragments (by score) are dispatched to ARGO."""
    # 15 fragments with distinct scores
    fragments = [
        _make_fragment(chunk_id=i, score=float(i) / 15.0, sha="a" * 63 + str(i % 10))
        for i in range(1, 16)
    ]
    response = _make_argo_response()
    client = _fake_client(response)
    summariser = ContextualSummarizer(client, top_k=DEFAULT_SUMMARISER_TOP_K)

    results = summariser.summarize_batch("What is the effect of yeast on cider?", fragments)

    # ARGO must have been called at most top_k times
    assert client.chat.call_count <= DEFAULT_SUMMARISER_TOP_K
    assert len(results) <= DEFAULT_SUMMARISER_TOP_K


def test_summariser_selects_highest_scored_fragments() -> None:
    """The fragments sent to ARGO are the ones with the highest scores."""
    low_score = _make_fragment(chunk_id=1, score=0.1, sha="b" * 64, text="low score fragment text")
    high_score = _make_fragment(
        chunk_id=2, score=0.95, sha="c" * 64, text="high score fragment text"
    )

    captured_texts: list[str] = []

    def capturing_chat(messages: list[dict[str, str]], **_: Any) -> MagicMock:
        # The user message contains the fragment text between delimiters
        user_msg = next(m for m in messages if m["role"] == "user")
        captured_texts.append(user_msg["content"])
        return _make_argo_response()

    client = MagicMock()
    client.chat.side_effect = capturing_chat
    summariser = ContextualSummarizer(client, top_k=1)

    summariser.summarize_batch("question", [low_score, high_score])

    # Only the high-score fragment should have been sent
    assert client.chat.call_count == 1
    assert high_score.text in captured_texts[0]
    assert low_score.text not in captured_texts[0]


def test_summariser_assigns_relevance_score() -> None:
    """Each result has a relevance_score in [0.0, 1.0]."""
    fragment = _make_fragment()
    client = _fake_client(_make_argo_response(score=0.73))
    summariser = ContextualSummarizer(client)

    results = summariser.summarize_batch("Quels sont les effets des tanins?", [fragment])

    assert len(results) == 1
    assert 0.0 <= results[0].relevance_score <= 1.0
    assert results[0].relevance_score == pytest.approx(0.73)


def test_summariser_works_without_argo() -> None:
    """When client=None the summariser returns an empty list (DRS-009: optional stage)."""
    fragments = [_make_fragment(chunk_id=i) for i in range(1, 4)]
    summariser = ContextualSummarizer(client=None)

    results = summariser.summarize_batch("Any question", fragments)

    assert results == []


def test_enabled_production_stage_uses_quota_managed_argo_client(settings) -> None:
    deep_research = settings.deep_research.model_copy(
        update={
            "contextual_summary_enabled": True,
            "contextual_relevance_observations_sha256": "a" * 64,
        }
    )
    enabled = settings.model_copy(update={"deep_research": deep_research})

    operations = build_deep_research_operations(enabled)

    assert isinstance(operations.contextual_summarizer.client, ArgoClient)
    assert operations.contextual_summarizer.client._quota_service is not None
    operations.close()


def test_summariser_skips_fragment_on_argo_error() -> None:
    """An ARGO error on one fragment must not crash the batch."""
    good_sha = "d" * 64
    bad_sha = "e" * 64
    good_fragment = _make_fragment(chunk_id=1, sha=good_sha, score=0.8)
    bad_fragment = _make_fragment(chunk_id=2, sha=bad_sha, score=0.9)

    call_count = 0

    def flaky_chat(messages: list[dict[str, str]], **_: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        # Fail on the first call (highest-scored fragment = bad_fragment)
        if call_count == 1:
            raise RuntimeError("Simulated ARGO error")
        return _make_argo_response()

    client = MagicMock()
    client.chat.side_effect = flaky_chat
    summariser = ContextualSummarizer(client, top_k=2)

    results = summariser.summarize_batch("question", [good_fragment, bad_fragment])

    # Only the successful call survives
    assert len(results) == 1
    assert results[0].text_sha256 == good_sha


def test_evaluation_summariser_propagates_argo_error_in_strict_mode() -> None:
    client = MagicMock()
    client.chat.side_effect = RuntimeError("quota or transport failure")
    summariser = ContextualSummarizer(client, strict_errors=True)

    with pytest.raises(RuntimeError, match="quota or transport"):
        summariser.summarize_batch("question", [_make_fragment()])


def test_no_fragment_text_in_returned_results() -> None:
    """Fragment text must not appear in ContextualSummaryResult."""
    fragment_text = "full-text sentinel must not leak"
    fragment = _make_fragment(scope=CorpusScope.COMMON, text=fragment_text)
    client = _fake_client(_make_argo_response(summary="Safe summary."))
    summariser = ContextualSummarizer(client)

    results = summariser.summarize_batch("question", [fragment])

    assert len(results) == 1
    result_json = results[0].model_dump_json()
    assert fragment_text not in result_json


def test_summariser_fragment_from_hit_and_text() -> None:
    """SummarisableFragment.from_hit_and_text preserves all hit fields."""
    hit = DeepResearchFragmentHit(
        method="vector",
        scope=CorpusScope.COMMON,
        article_id="art-42",
        chunk_id=7,
        page_start=3,
        page_end=4,
        score=0.85,
        text_sha256="f" * 64,
    )
    fragment = SummarisableFragment.from_hit_and_text(hit, "some text")

    assert fragment.method == hit.method
    assert fragment.scope == hit.scope
    assert fragment.article_id == hit.article_id
    assert fragment.chunk_id == hit.chunk_id
    assert fragment.page_start == hit.page_start
    assert fragment.page_end == hit.page_end
    assert fragment.text == "some text"


# ---------------------------------------------------------------------------
# DRS-010 tests
# ---------------------------------------------------------------------------


def _make_result(relevance_score: float, chunk_id: int = 1) -> ContextualSummaryResult:
    return ContextualSummaryResult(
        text_sha256="a" * 64,
        article_id="art-001",
        chunk_id=chunk_id,
        scope=CorpusScope.COMMON,
        page_start=1,
        page_end=2,
        summary="A fragment summary.",
        relevance_score=relevance_score,
        relevant=relevance_score >= 0.5,
    )


def test_filter_removes_irrelevant_summaries() -> None:
    """filter_relevant keeps only results with relevant=True."""
    above = _make_result(0.8, chunk_id=1)
    below = _make_result(0.3, chunk_id=2)

    filtered = filter_relevant([above, below])

    assert filtered == [above]
    assert below not in filtered


def test_filter_empty_list_is_safe() -> None:
    assert filter_relevant([]) == []


def test_filter_all_relevant() -> None:
    results = [_make_result(0.9, chunk_id=i) for i in range(1, 4)]
    assert filter_relevant(results) == results


def test_filter_none_relevant() -> None:
    results = [_make_result(0.1, chunk_id=i) for i in range(1, 4)]
    assert filter_relevant(results) == []


def test_rejected_summary_cannot_become_evidence() -> None:
    """A summary below the threshold must not appear in the filtered output.

    This mirrors the DRS-010 invariant: a rejected summary can never become
    a proof passed to the synthesis step.
    """
    threshold = 0.5
    fragments = [
        _make_fragment(chunk_id=1, score=0.9, sha="1" * 64, text="relevant text"),
        _make_fragment(chunk_id=2, score=0.85, sha="2" * 64, text="irrelevant text"),
    ]

    call_index = 0

    def scoring_chat(messages: list[dict[str, str]], **_: Any) -> MagicMock:
        nonlocal call_index
        # First call (highest scored) → above threshold
        # Second call → below threshold
        score = 0.8 if call_index == 0 else 0.2
        call_index += 1
        return _make_argo_response(score=score)

    client = MagicMock()
    client.chat.side_effect = scoring_chat
    summariser = ContextualSummarizer(client, relevance_threshold=threshold)

    raw_results = summariser.summarize_batch("question", fragments)
    evidence = filter_relevant(raw_results)

    assert len(raw_results) == 2
    # Only the relevant result should be in evidence
    assert all(r.relevant for r in evidence)
    assert all(r.relevance_score >= threshold for r in evidence)
    # The rejected summary is completely absent from the evidence list
    rejected = [r for r in raw_results if not r.relevant]
    for rejected_result in rejected:
        assert rejected_result not in evidence

    gate = build_contextual_evidence(raw_results, threshold=threshold)
    assert gate.accepted == evidence
    assert gate.rejected_summary_count == 1
    with pytest.raises(ValidationError, match="rejected contextual summary"):
        ContextualEvidenceGate(
            threshold=threshold,
            source_summary_count=1,
            rejected_summary_count=0,
            accepted=rejected,
        )


def test_summariser_clamps_score_to_valid_range() -> None:
    """ARGO scores outside [0,1] are clamped, not rejected."""
    fragment = _make_fragment()
    mock_response = MagicMock()
    mock_response.content = json.dumps({"summary": "Good.", "relevance_score": 1.5})
    client = _fake_client(mock_response)
    summariser = ContextualSummarizer(client)

    results = summariser.summarize_batch("question", [fragment])

    assert len(results) == 1
    assert results[0].relevance_score == pytest.approx(1.0)
