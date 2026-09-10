"""Semantic verification of every atomic claim against its verbatim evidence."""

from __future__ import annotations

from pathlib import Path

from app.deep_research.claims import AtomicClaim, AtomicClaimCheckpoint
from app.jobs.contracts import DeepResearchPayload
from app.llm.claim_verification import (
    ClaimSemanticVerification,
    ClaimVerifier,
    SemanticVerificationCheckpoint,
    SemanticVerificationClient,
    SemanticVerificationError,
)
from app.llm.claim_verification import (
    SemanticDimensionCheck as SemanticDimensionCheck,
)


class SemanticClaimVerificationStage:
    def __init__(
        self,
        client: SemanticVerificationClient | None,
        checkpoint_root: Path,
    ) -> None:
        self.client = client
        self.checkpoint_root = checkpoint_root

    def _path(self, payload: DeepResearchPayload) -> Path:
        return (
            self.checkpoint_root
            / str(payload.conversation_id)
            / str(payload.client_request_id)
            / "semantic-verification.json"
        )

    def load(self, payload: DeepResearchPayload) -> SemanticVerificationCheckpoint:
        path = self._path(payload)
        if not path.is_file():
            raise RuntimeError("deep-research semantic-verification checkpoint is missing")
        return SemanticVerificationCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def verify(
        self,
        payload: DeepResearchPayload,
        claims: AtomicClaimCheckpoint,
    ) -> SemanticVerificationCheckpoint:
        path = self._path(payload)
        if path.is_file():
            checkpoint = self.load(payload)
            self._validate_coverage(claims.claims, checkpoint.verifications)
            return checkpoint
        verifications: list[ClaimSemanticVerification] = []
        if self.client is not None and claims.claims:
            verifications = ClaimVerifier(self.client).verify(
                payload.message,
                [
                    {
                        "claim_id": claim.claim_id,
                        "statement": claim.statement,
                        "role": claim.role,
                        "verbatim_evidence": [
                            evidence.source_excerpt for evidence in claim.evidence
                        ],
                    }
                    for claim in claims.claims
                ],
            )
        elif claims.claims:
            raise SemanticVerificationError(
                "atomic claims cannot be verified without the configured verification client"
            )
        checkpoint = SemanticVerificationCheckpoint(verifications=verifications)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return checkpoint

    @staticmethod
    def _validate_coverage(
        claims: list[AtomicClaim],
        verifications: list[ClaimSemanticVerification],
    ) -> None:
        expected = {claim.claim_id for claim in claims}
        observed = {item.claim_id for item in verifications}
        if len(observed) != len(verifications) or observed != expected:
            raise ValueError("every and only atomic claim must be verified exactly once")
