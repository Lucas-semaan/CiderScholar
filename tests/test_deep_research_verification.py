from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.corpora import CorpusScope
from app.deep_research.claims import (
    AtomicClaim,
    AtomicClaimCheckpoint,
    AtomicClaimEvidence,
)
from app.deep_research.verification import (
    SemanticClaimVerificationStage,
    SemanticVerificationError,
)
from app.jobs.contracts import DeepResearchPayload


class _Client:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def chat(self, _messages, **_kwargs):
        return SimpleNamespace(content=json.dumps(self.payload))


def _payload() -> DeepResearchPayload:
    return DeepResearchPayload(
        message="Quel effet a été observé ?",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )


def _claims() -> AtomicClaimCheckpoint:
    return AtomicClaimCheckpoint(
        question_sha256="a" * 64,
        source_fragment_count=1,
        claims=[
            AtomicClaim(
                claim_id="claim-" + "b" * 20,
                statement="La concentration atteint 4 mg/L après dix jours.",
                role="result",
                evidence=[
                    AtomicClaimEvidence(
                        scope=CorpusScope.COMMON,
                        article_id="article-verify",
                        chunk_id=3,
                        page_start=2,
                        page_end=2,
                        source_excerpt="La concentration atteint 4 mg/L après dix jours.",
                        source_text_sha256="c" * 64,
                    )
                ],
            )
        ],
    )


def _check(status: str = "entailed") -> dict[str, str]:
    return {"status": status, "reason": f"Statut contrôlé : {status}."}


def _verification(*, unit: str = "entailed") -> dict[str, object]:
    return {
        "claim_id": "claim-" + "b" * 20,
        "implication": _check(),
        "negation": _check("not_applicable"),
        "unit": _check(unit),
        "population": _check("not_applicable"),
        "condition": _check("not_applicable"),
        "temporality": _check(),
    }


def test_all_six_dimensions_are_persisted_before_support(tmp_path) -> None:
    stage = SemanticClaimVerificationStage(
        _Client({"verifications": [_verification()]}),
        tmp_path,
    )
    payload = _payload()

    checkpoint = stage.verify(payload, _claims())
    result = checkpoint.verifications[0]

    assert result.supported is True
    assert result.implication.status == "entailed"
    assert result.negation.status == "not_applicable"
    assert result.unit.status == "entailed"
    assert result.population.status == "not_applicable"
    assert result.condition.status == "not_applicable"
    assert result.temporality.status == "entailed"
    assert stage.load(payload) == checkpoint


def test_one_contradicted_dimension_makes_claim_unsupported(tmp_path) -> None:
    stage = SemanticClaimVerificationStage(
        _Client({"verifications": [_verification(unit="contradicted")]}),
        tmp_path,
    )

    checkpoint = stage.verify(_payload(), _claims())

    assert checkpoint.verifications[0].supported is False


def test_missing_claim_verification_is_rejected(tmp_path) -> None:
    stage = SemanticClaimVerificationStage(
        _Client({"verifications": []}),
        tmp_path,
    )

    with pytest.raises(SemanticVerificationError, match="incomplete"):
        stage.verify(_payload(), _claims())


def test_claims_cannot_pass_when_verification_client_is_disabled(tmp_path) -> None:
    stage = SemanticClaimVerificationStage(None, tmp_path)

    with pytest.raises(SemanticVerificationError, match="without"):
        stage.verify(_payload(), _claims())
