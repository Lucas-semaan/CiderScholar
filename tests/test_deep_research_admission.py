from __future__ import annotations

from uuid import uuid4

from app.deep_research.admission import ClaimAdmissionStage
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
        message="Filtrer les affirmations.",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )


def _claim(index: int) -> AtomicClaim:
    statement = f"Résultat local numéro {index}."
    return AtomicClaim(
        claim_id=f"claim-{index:020x}",
        statement=statement,
        role="result",
        evidence=[
            AtomicClaimEvidence(
                scope="common",
                article_id="article-admission",
                chunk_id=index,
                page_start=1,
                page_end=1,
                source_excerpt=statement,
                source_text_sha256=f"{index:064x}",
            )
        ],
    )


def _verification(claim_id: str, *, implication: str) -> ClaimSemanticVerification:
    implication_check = SemanticDimensionCheck(
        status=implication,
        reason="Implication contrôlée.",
    )
    neutral = SemanticDimensionCheck(status="not_applicable", reason="Non applicable.")
    supported = implication == "entailed"
    return ClaimSemanticVerification(
        claim_id=claim_id,
        implication=implication_check,
        negation=neutral,
        unit=neutral,
        population=neutral,
        condition=neutral,
        temporality=neutral,
        supported=supported,
    )


def test_non_implicating_citation_never_enters_synthesis(tmp_path) -> None:
    claims = AtomicClaimCheckpoint(
        question_sha256="a" * 64,
        source_fragment_count=2,
        claims=[_claim(1), _claim(2)],
    )
    verifications = SemanticVerificationCheckpoint(
        verifications=[
            _verification(claims.claims[0].claim_id, implication="entailed"),
            _verification(claims.claims[1].claim_id, implication="uncertain"),
        ]
    )
    payload = _payload()
    epistemic = EpistemicAssessmentStage(tmp_path).assess(
        payload,
        claims,
        verifications,
    )
    stage = ClaimAdmissionStage(tmp_path)

    checkpoint = stage.decide(
        payload,
        claims,
        verifications,
        epistemic,
    )
    admitted = stage.admitted_claims(claims, checkpoint)

    assert [item.status for item in checkpoint.decisions] == ["accepted", "rejected"]
    assert checkpoint.decisions[1].reason == "implication_not_entailed"
    assert checkpoint.decisions[1].admitted_statement is None
    assert admitted == (claims.claims[0],)
    assert stage.load(payload) == checkpoint
