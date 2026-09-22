from __future__ import annotations

from app.corpora import CorpusScope
from app.evaluation.evidence_gaps import EvidenceGap, EvidenceGapPlan
from app.evaluation.second_wave import run_second_wave
from app.retrieval.chat_checkpoint import ChatCheckpointEvidence, ChatCheckpointPassage


def _record(record_id: str, grade: str) -> ChatCheckpointEvidence:
    return ChatCheckpointEvidence(
        record_id=record_id,
        evidence_level="full_text",
        scope=CorpusScope.COMMON,
        article_id="article-1",
        evidence_grade=grade,
        passages=[
            ChatCheckpointPassage(
                evidence_id=f"{record_id}:1",
                chunk_id=1,
                page_start=1,
                page_end=1,
                locator_kind="page",
            )
        ],
    )


def test_second_wave_is_limited_to_gaps_and_keeps_only_new_a_b_records() -> None:
    plan = EvidenceGapPlan(
        gaps=(
            EvidenceGap(kind="missing_outcome", query="ester outcome", reason="Missing outcome."),
        )
    )
    calls: list[str] = []

    result = run_second_wave(
        plan,
        retrieve_sqlite=lambda query: (
            calls.append(query) or [_record("old", "A"), _record("keep", "B"), _record("drop", "C")]
        ),
        first_wave_record_ids=("old",),
    )

    assert calls == ["ester outcome"]
    assert result.query_count == 1
    assert [record.record_id for record in result.evidence] == ["keep"]
