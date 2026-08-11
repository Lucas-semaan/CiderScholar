"""Fail-closed admission of semantically supported claims into synthesis."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.deep_research.claims import AtomicClaim, AtomicClaimCheckpoint
from app.deep_research.epistemic import EpistemicAssessmentCheckpoint
from app.deep_research.numeric import (
    DeepResearchNumericVerificationStage,
    NumericVerificationCheckpoint,
)
from app.deep_research.verification import SemanticVerificationCheckpoint
from app.jobs.contracts import DeepResearchPayload


class ClaimAdmissionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^claim-[0-9a-f]{20}$")
    status: Literal["accepted", "rejected"]
    reason: Literal[
        "semantically_supported",
        "implication_not_entailed",
        "semantic_dimension_not_supported",
        "numeric_not_supported",
        "hypothesis_not_admitted_as_fact",
    ]
    admitted_statement: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def keep_rejected_text_out_of_synthesis(self) -> ClaimAdmissionDecision:
        if self.status == "accepted":
            if self.reason != "semantically_supported" or not self.admitted_statement:
                raise ValueError("accepted claim requires its supported statement")
        elif self.admitted_statement is not None or self.reason == "semantically_supported":
            raise ValueError("rejected claim cannot expose a statement to synthesis")
        return self


class ClaimAdmissionCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    decisions: list[ClaimAdmissionDecision] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_claims(self) -> ClaimAdmissionCheckpoint:
        claim_ids = [item.claim_id for item in self.decisions]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim admission decisions cannot be duplicated")
        return self


class ClaimAdmissionStage:
    def __init__(self, checkpoint_root: Path) -> None:
        self.checkpoint_root = checkpoint_root

    def _path(self, payload: DeepResearchPayload) -> Path:
        return (
            self.checkpoint_root
            / str(payload.conversation_id)
            / str(payload.client_request_id)
            / "claim-admission-v2.json"
        )

    def load(self, payload: DeepResearchPayload) -> ClaimAdmissionCheckpoint:
        path = self._path(payload)
        if not path.is_file():
            raise RuntimeError("deep-research claim-admission checkpoint is missing")
        return ClaimAdmissionCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def decide(
        self,
        payload: DeepResearchPayload,
        claims: AtomicClaimCheckpoint,
        verifications: SemanticVerificationCheckpoint,
        epistemic: EpistemicAssessmentCheckpoint,
        numeric: NumericVerificationCheckpoint | None = None,
    ) -> ClaimAdmissionCheckpoint:
        path = self._path(payload)
        if path.is_file():
            checkpoint = self.load(payload)
            self._validate_coverage(claims, checkpoint)
            return checkpoint
        verification_by_id = {item.claim_id: item for item in verifications.verifications}
        level_by_id = {item.claim_id: item.level for item in epistemic.assessments}
        numeric_by_id = {item.claim_id: item for item in numeric.verifications} if numeric else {}
        claim_ids = {claim.claim_id for claim in claims.claims}
        if set(verification_by_id) != claim_ids or set(level_by_id) != claim_ids:
            raise ValueError("claim admission requires complete verification and epistemic levels")
        if numeric is not None and set(numeric_by_id) != claim_ids:
            raise ValueError("claim admission requires complete numeric verification")
        checkpoint = ClaimAdmissionCheckpoint(
            decisions=[
                self._decision(
                    claim,
                    verification_by_id[claim.claim_id],
                    level_by_id[claim.claim_id],
                    numeric_by_id.get(claim.claim_id),
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
    def _decision(claim, verification, level, numeric=None) -> ClaimAdmissionDecision:
        numeric_supported = numeric is None or DeepResearchNumericVerificationStage.is_admissible(
            numeric
        )
        if verification.supported and level != "hypothese" and numeric_supported:
            return ClaimAdmissionDecision(
                claim_id=claim.claim_id,
                status="accepted",
                reason="semantically_supported",
                admitted_statement=claim.statement,
            )
        if verification.implication.status != "entailed":
            reason = "implication_not_entailed"
        elif not verification.supported:
            reason = "semantic_dimension_not_supported"
        elif not numeric_supported:
            reason = "numeric_not_supported"
        else:
            reason = "hypothesis_not_admitted_as_fact"
        return ClaimAdmissionDecision(
            claim_id=claim.claim_id,
            status="rejected",
            reason=reason,
        )

    @staticmethod
    def _validate_coverage(
        claims: AtomicClaimCheckpoint,
        checkpoint: ClaimAdmissionCheckpoint,
    ) -> None:
        if {claim.claim_id for claim in claims.claims} != {
            item.claim_id for item in checkpoint.decisions
        }:
            raise ValueError("claim admission checkpoint does not cover the current claims")

    @staticmethod
    def admitted_claims(
        claims: AtomicClaimCheckpoint,
        checkpoint: ClaimAdmissionCheckpoint,
    ) -> tuple[AtomicClaim, ...]:
        accepted = {item.claim_id for item in checkpoint.decisions if item.status == "accepted"}
        return tuple(claim for claim in claims.claims if claim.claim_id in accepted)
