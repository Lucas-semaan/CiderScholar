"""Application-computed epistemic levels for semantically verified claims."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.deep_research.claims import AtomicClaim, AtomicClaimCheckpoint
from app.deep_research.verification import (
    ClaimSemanticVerification,
    SemanticVerificationCheckpoint,
)
from app.jobs.contracts import DeepResearchPayload

EpistemicLevel = Literal["observation_directe", "deduction", "hypothese"]


def _computed_level(
    claim: AtomicClaim,
    verification: ClaimSemanticVerification,
) -> EpistemicLevel:
    if not verification.supported:
        return "hypothese"
    if claim.role == "result":
        return "observation_directe"
    return "deduction"


class EpistemicClaimAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^claim-[0-9a-f]{20}$")
    source_role: Literal["result", "interpretation", "recommendation"]
    semantically_supported: bool
    level: EpistemicLevel

    @model_validator(mode="after")
    def enforce_application_rule(self) -> EpistemicClaimAssessment:
        expected: EpistemicLevel
        if not self.semantically_supported:
            expected = "hypothese"
        elif self.source_role == "result":
            expected = "observation_directe"
        else:
            expected = "deduction"
        if self.level != expected:
            raise ValueError("epistemic level does not match the application rule")
        return self


class EpistemicAssessmentCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    assessments: list[EpistemicClaimAssessment] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_claims(self) -> EpistemicAssessmentCheckpoint:
        claim_ids = [item.claim_id for item in self.assessments]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("epistemic claim assessments cannot be duplicated")
        return self


class EpistemicAssessmentStage:
    def __init__(self, checkpoint_root: Path) -> None:
        self.checkpoint_root = checkpoint_root

    def _path(self, payload: DeepResearchPayload) -> Path:
        return (
            self.checkpoint_root
            / str(payload.conversation_id)
            / str(payload.client_request_id)
            / "epistemic-assessment.json"
        )

    def load(self, payload: DeepResearchPayload) -> EpistemicAssessmentCheckpoint:
        path = self._path(payload)
        if not path.is_file():
            raise RuntimeError("deep-research epistemic checkpoint is missing")
        return EpistemicAssessmentCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def assess(
        self,
        payload: DeepResearchPayload,
        claims: AtomicClaimCheckpoint,
        verifications: SemanticVerificationCheckpoint,
    ) -> EpistemicAssessmentCheckpoint:
        path = self._path(payload)
        if path.is_file():
            checkpoint = self.load(payload)
            self._validate_coverage(claims, checkpoint)
            return checkpoint
        verification_by_claim = {item.claim_id: item for item in verifications.verifications}
        if set(verification_by_claim) != {claim.claim_id for claim in claims.claims}:
            raise ValueError("epistemic assessment requires every semantic verification")
        checkpoint = EpistemicAssessmentCheckpoint(
            assessments=[
                EpistemicClaimAssessment(
                    claim_id=claim.claim_id,
                    source_role=claim.role,
                    semantically_supported=verification_by_claim[claim.claim_id].supported,
                    level=_computed_level(claim, verification_by_claim[claim.claim_id]),
                )
                for claim in claims.claims
            ]
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return checkpoint

    @staticmethod
    def _validate_coverage(
        claims: AtomicClaimCheckpoint,
        checkpoint: EpistemicAssessmentCheckpoint,
    ) -> None:
        if {claim.claim_id for claim in claims.claims} != {
            item.claim_id for item in checkpoint.assessments
        }:
            raise ValueError("epistemic checkpoint does not cover the current claims")

    def public_details(
        self,
        payload: DeepResearchPayload,
        claims: AtomicClaimCheckpoint,
    ) -> list[dict[str, str]]:
        checkpoint = self.load(payload)
        levels = {item.claim_id: item.level for item in checkpoint.assessments}
        if set(levels) != {claim.claim_id for claim in claims.claims}:
            raise ValueError("epistemic details do not cover the current claims")
        return [
            {
                "claim_id": claim.claim_id,
                "statement": claim.statement,
                "level": levels[claim.claim_id],
            }
            for claim in claims.claims
        ]
