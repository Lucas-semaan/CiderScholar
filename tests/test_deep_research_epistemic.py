from __future__ import annotations

from uuid import uuid4

from app.deep_research.claims import AtomicClaim, AtomicClaimCheckpoint, AtomicClaimEvidence
from app.deep_research.epistemic import EpistemicAssessmentStage
from app.deep_research.verification import (
    ClaimSemanticVerification,
    SemanticDimensionCheck,
    SemanticVerificationCheckpoint,
)
from app.jobs.contracts import DeepResearchPayload


def _payload() -> DeepResearchPayload:
    return DeepResearchPayload(
        message="Classer le niveau épistémique.",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )


def _claim(index: int, role: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id=f"claim-{index:020x}",
        statement=f"Affirmation {index}.",
        role=role,
        evidence=[
            AtomicClaimEvidence(
                scope="common",
                article_id="article-level",
                chunk_id=index,
                page_start=1,
                page_end=1,
                source_excerpt=f"Affirmation {index}.",
                source_text_sha256=f"{index:064x}",
            )
        ],
    )


def _verification(claim_id: str, supported: bool) -> ClaimSemanticVerification:
    entailed = SemanticDimensionCheck(status="entailed", reason="Impliqué.")
    neutral = SemanticDimensionCheck(status="not_applicable", reason="Non applicable.")
    uncertain = SemanticDimensionCheck(status="uncertain", reason="Non démontré.")
    return ClaimSemanticVerification(
        claim_id=claim_id,
        implication=entailed if supported else uncertain,
        negation=neutral,
        unit=neutral,
        population=neutral,
        condition=neutral,
        temporality=neutral,
        supported=supported,
    )


def test_epistemic_levels_are_computed_and_visible_in_details(tmp_path) -> None:
    claims = AtomicClaimCheckpoint(
        question_sha256="a" * 64,
        source_fragment_count=3,
        claims=[
            _claim(1, "result"),
            _claim(2, "interpretation"),
            _claim(3, "recommendation"),
        ],
    )
    verifications = SemanticVerificationCheckpoint(
        verifications=[
            _verification(claims.claims[0].claim_id, True),
            _verification(claims.claims[1].claim_id, True),
            _verification(claims.claims[2].claim_id, False),
        ]
    )
    stage = EpistemicAssessmentStage(tmp_path)
    payload = _payload()

    checkpoint = stage.assess(payload, claims, verifications)
    details = stage.public_details(payload, claims)

    assert [item.level for item in checkpoint.assessments] == [
        "observation_directe",
        "deduction",
        "hypothese",
    ]
    assert [item["level"] for item in details] == [
        "observation_directe",
        "deduction",
        "hypothese",
    ]
    assert details[0]["statement"] == "Affirmation 1."
