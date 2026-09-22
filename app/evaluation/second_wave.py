"""Evaluation-only bounded second retrieval wave over already-local evidence."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.evaluation.evidence_gaps import EvidenceGapPlan
from app.retrieval.chat_checkpoint import ChatCheckpointEvidence


class SecondWaveResult(BaseModel):
    """Traceable A/B-only evidence returned by one bounded evaluation wave."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query_count: int = Field(ge=1, le=3)
    evidence: tuple[ChatCheckpointEvidence, ...] = Field(default=(), max_length=48)


def run_second_wave(
    plan: EvidenceGapPlan,
    *,
    retrieve_sqlite: Callable[[str], Sequence[ChatCheckpointEvidence]],
    first_wave_record_ids: Sequence[str] = (),
) -> SecondWaveResult:
    """Retrieve only persisted SQLite identities, then deduplicate and retain A/B evidence."""

    seen = set(first_wave_record_ids)
    selected: list[ChatCheckpointEvidence] = []
    for gap in plan.gaps:
        for record in retrieve_sqlite(gap.query):
            if record.record_id in seen or record.evidence_grade not in {"A", "B"}:
                continue
            seen.add(record.record_id)
            selected.append(record)
    return SecondWaveResult(query_count=len(plan.gaps), evidence=tuple(selected))
