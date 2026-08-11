"""Versioned deterministic numeric verification for atomic deep-research claims."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.deep_research.claims import AtomicClaim, AtomicClaimCheckpoint
from app.jobs.contracts import DeepResearchPayload
from app.numeric_verification import NumericIssueCode, NumericVerdict, verify_numeric_claim

NUMERIC_VERIFICATION_ALGORITHM_VERSION = 1
_ADMISSIBLE_VERDICTS = frozenset({NumericVerdict.SUPPORTED, NumericVerdict.NOT_APPLICABLE})


class NumericClaimVerification(BaseModel):
    """Text-free numeric-verification outcome for exactly one atomic claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^claim-[0-9a-f]{20}$")
    verdict: NumericVerdict
    issue_codes: list[NumericIssueCode] = Field(default_factory=list, max_length=16)
    unparsed_numeric_count: int = Field(ge=0, le=100)


class NumericVerificationCheckpoint(BaseModel):
    """A replay-safe checkpoint containing no scientific excerpts or answer text."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    algorithm_version: Literal[1] = NUMERIC_VERIFICATION_ALGORITHM_VERSION
    claims_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifications: list[NumericClaimVerification] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_claims(self) -> NumericVerificationCheckpoint:
        claim_ids = [item.claim_id for item in self.verifications]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("numeric verification claim identifiers cannot be duplicated")
        return self


def _claims_fingerprint(claims: AtomicClaimCheckpoint) -> str:
    """Bind the checkpoint to statements and evidence identities without persisting excerpts."""

    payload = [
        {
            "claim_id": claim.claim_id,
            "statement_sha256": hashlib.sha256(claim.statement.encode("utf-8")).hexdigest(),
            "evidence": [
                {
                    "kind": item.evidence_kind,
                    "scope": item.scope.value,
                    "article_id": item.article_id,
                    "chunk_id": item.chunk_id,
                    "source_text_sha256": item.source_text_sha256,
                    "figure_analysis_id": item.figure_analysis_id,
                }
                for item in claim.evidence
            ],
        }
        for claim in claims.claims
    ]
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class DeepResearchNumericVerificationStage:
    """Persist and validate deterministic numeric checks for replayable deep jobs."""

    def __init__(self, checkpoint_root: Path) -> None:
        self.checkpoint_root = checkpoint_root

    def _path(self, payload: DeepResearchPayload) -> Path:
        return (
            self.checkpoint_root
            / str(payload.conversation_id)
            / str(payload.client_request_id)
            / "numeric-verification-v1.json"
        )

    def load(
        self,
        payload: DeepResearchPayload,
        claims: AtomicClaimCheckpoint,
    ) -> NumericVerificationCheckpoint:
        path = self._path(payload)
        if not path.is_file():
            raise RuntimeError("deep-research numeric-verification checkpoint is missing")
        checkpoint = NumericVerificationCheckpoint.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        self._validate_coverage(claims, checkpoint)
        return checkpoint

    def verify(
        self,
        payload: DeepResearchPayload,
        claims: AtomicClaimCheckpoint,
    ) -> NumericVerificationCheckpoint:
        path = self._path(payload)
        if path.is_file():
            return self.load(payload, claims)
        checkpoint = NumericVerificationCheckpoint(
            claims_sha256=_claims_fingerprint(claims),
            verifications=[self._verify_claim(claim) for claim in claims.claims],
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return checkpoint

    @staticmethod
    def _verify_claim(claim: AtomicClaim) -> NumericClaimVerification:
        report = verify_numeric_claim(
            claim.statement,
            {
                f"evidence-{index}": item.source_excerpt
                for index, item in enumerate(claim.evidence, start=1)
            },
        )
        return NumericClaimVerification(
            claim_id=claim.claim_id,
            verdict=report.verdict,
            issue_codes=list(report.issues),
            unparsed_numeric_count=report.unparsed_numeric_count,
        )

    @staticmethod
    def _validate_coverage(
        claims: AtomicClaimCheckpoint,
        checkpoint: NumericVerificationCheckpoint,
    ) -> None:
        if checkpoint.claims_sha256 != _claims_fingerprint(claims):
            raise RuntimeError("numeric verification checkpoint does not match current claims")
        if {claim.claim_id for claim in claims.claims} != {
            item.claim_id for item in checkpoint.verifications
        }:
            raise RuntimeError("numeric verification checkpoint does not cover current claims")

    @staticmethod
    def is_admissible(verification: NumericClaimVerification) -> bool:
        return verification.verdict in _ADMISSIBLE_VERDICTS

    @classmethod
    def assert_admission_safe(cls, admission, checkpoint: NumericVerificationCheckpoint) -> None:
        """Prevent a tampered or stale numeric checkpoint from rendering an accepted claim."""

        by_claim_id = {item.claim_id: item for item in checkpoint.verifications}
        for decision in admission.decisions:
            verification = by_claim_id.get(decision.claim_id)
            if decision.status == "accepted" and (
                verification is None or not cls.is_admissible(verification)
            ):
                raise RuntimeError("accepted deep-research claim lacks numeric support")

    @staticmethod
    def public_details(checkpoint: NumericVerificationCheckpoint) -> dict[str, object]:
        """Summarize outcomes for response telemetry without source text or source IDs."""

        verdict_counts = {verdict.value: 0 for verdict in NumericVerdict}
        issue_counts: dict[str, int] = {}
        for verification in checkpoint.verifications:
            verdict_counts[verification.verdict.value] += 1
            for issue in verification.issue_codes:
                issue_counts[issue.value] = issue_counts.get(issue.value, 0) + 1
        return {
            "algorithm_version": checkpoint.algorithm_version,
            "verdict_counts": verdict_counts,
            "issue_counts": issue_counts,
        }
