from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.evaluation.evidence_gaps import EvidenceGap, EvidenceGapPlan


def test_evidence_gap_plan_is_bounded_and_traceable() -> None:
    plan = EvidenceGapPlan(
        gaps=(
            EvidenceGap(
                kind="missing_outcome",
                query="fermentation cider ester concentration",
                reason="No eligible result measured the requested ester outcome.",
                history=("fermentation cider aroma",),
            ),
        )
    )

    assert plan.gaps[0].kind == "missing_outcome"


def test_evidence_gap_rejects_repeated_or_excess_queries() -> None:
    with pytest.raises(ValidationError, match="already present"):
        EvidenceGap(
            kind="missing_population",
            query="Cider fermentation",
            reason="The target population is not represented.",
            history=("cider fermentation",),
        )
    gap = EvidenceGap(
        kind="contradiction",
        query="cider aroma contradiction",
        reason="The measured direction conflicts between sources.",
    )
    with pytest.raises(ValidationError, match="at most 3"):
        EvidenceGapPlan(
            gaps=(
                gap,
                gap.model_copy(update={"query": "one"}),
                gap.model_copy(update={"query": "two"}),
                gap.model_copy(update={"query": "three"}),
            )
        )
