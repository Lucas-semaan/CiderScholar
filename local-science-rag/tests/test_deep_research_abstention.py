from __future__ import annotations

from uuid import uuid4

from app.deep_research.abstention import DeepResearchAbstentionStage
from app.deep_research.admission import (
    ClaimAdmissionCheckpoint,
    ClaimAdmissionDecision,
)
from app.deep_research.iteration import (
    MissingInformationAssessment,
    ResearchGap,
    ResearchIterationRecord,
    ResearchLoopCheckpoint,
)
from app.jobs.contracts import DeepResearchPayload


def _payload() -> DeepResearchPayload:
    return DeepResearchPayload(
        message="Peut-on conclure ?",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )


def _loop_with_gap(question: str) -> ResearchLoopCheckpoint:
    gap = ResearchGap.from_assessment(
        MissingInformationAssessment(
            sufficient=False,
            gap_description="La population étudiée n’est pas précisée.",
            follow_up_query="population étudiée fermentation cidre",
        )
    )
    return ResearchLoopCheckpoint(
        original_question=question,
        iterations=[
            ResearchIterationRecord(index=1, query=question),
            ResearchIterationRecord(index=2, query=gap.follow_up_query, gap=gap),
        ],
        stop_reason="maximum_iterations",
    )


def test_zero_admitted_claims_produces_explicit_gap_only_abstention(tmp_path) -> None:
    payload = _payload()
    stage = DeepResearchAbstentionStage(tmp_path)

    checkpoint = stage.decide(
        payload,
        _loop_with_gap(payload.message),
        ClaimAdmissionCheckpoint(decisions=[]),
    )

    assert checkpoint.outcome == "abstain"
    assert checkpoint.admitted_claim_count == 0
    assert checkpoint.gap_descriptions == ["La population étudiée n’est pas précisée."]
    assert "La population étudiée n’est pas précisée." in checkpoint.abstention_markdown
    assert "18 °C" not in checkpoint.abstention_markdown
    assert stage.load(payload) == checkpoint


def test_one_admitted_claim_makes_readiness_answerable_without_generated_text(
    tmp_path,
) -> None:
    payload = _payload()
    stage = DeepResearchAbstentionStage(tmp_path)
    admission = ClaimAdmissionCheckpoint(
        decisions=[
            ClaimAdmissionDecision(
                claim_id="claim-" + "a" * 20,
                status="accepted",
                reason="semantically_supported",
                admitted_statement="Résultat étayé.",
            )
        ]
    )

    checkpoint = stage.decide(
        payload,
        _loop_with_gap(payload.message),
        admission,
    )

    assert checkpoint.outcome == "answerable"
    assert checkpoint.admitted_claim_count == 1
    assert checkpoint.gap_descriptions == []
    assert checkpoint.abstention_markdown is None
