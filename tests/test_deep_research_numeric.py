from __future__ import annotations

from uuid import uuid4

import pytest

from app.deep_research.claims import AtomicClaim, AtomicClaimCheckpoint, AtomicClaimEvidence
from app.deep_research.numeric import DeepResearchNumericVerificationStage
from app.jobs.contracts import DeepResearchPayload
from app.numeric_verification import NumericVerdict


def _payload() -> DeepResearchPayload:
    return DeepResearchPayload(
        message="Vérifier une valeur de pH.",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )


def _claims(*, statement: str, evidence: str) -> AtomicClaimCheckpoint:
    return AtomicClaimCheckpoint(
        question_sha256="a" * 64,
        source_fragment_count=1,
        claims=[
            AtomicClaim(
                claim_id="claim-00000000000000000001",
                statement=statement,
                role="result",
                evidence=[
                    AtomicClaimEvidence(
                        scope="common",
                        article_id="article-numeric",
                        chunk_id=1,
                        page_start=1,
                        page_end=1,
                        source_excerpt=evidence,
                        source_text_sha256="b" * 64,
                    )
                ],
            )
        ],
    )


def test_numeric_checkpoint_is_text_free_and_replay_safe(tmp_path) -> None:
    payload = _payload()
    claims = _claims(statement="Le pH est 3,5.", evidence="The pH was 3.5.")
    stage = DeepResearchNumericVerificationStage(tmp_path)

    checkpoint = stage.verify(payload, claims)

    assert checkpoint.verifications[0].verdict is NumericVerdict.SUPPORTED
    persisted = stage._path(payload).read_text(encoding="utf-8")
    assert "The pH was" not in persisted
    assert stage.load(payload, claims) == checkpoint
    changed = _claims(statement="Le pH est 3,4.", evidence="The pH was 3.5.")
    with pytest.raises(RuntimeError, match="does not match current claims"):
        stage.load(payload, changed)


def test_numeric_checkpoint_rejects_an_incompatible_quantity(tmp_path) -> None:
    checkpoint = DeepResearchNumericVerificationStage(tmp_path).verify(
        _payload(),
        _claims(statement="Le pH est 3,5.", evidence="The pH was 4.5."),
    )

    assert checkpoint.verifications[0].verdict is NumericVerdict.UNSUPPORTED
    assert DeepResearchNumericVerificationStage.is_admissible(checkpoint.verifications[0]) is False
