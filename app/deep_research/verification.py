"""Semantic verification of every atomic claim against its verbatim evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.deep_research.claims import AtomicClaim, AtomicClaimCheckpoint
from app.jobs.contracts import DeepResearchPayload

SemanticCheckStatus = Literal["entailed", "contradicted", "uncertain", "not_applicable"]


class SemanticDimensionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SemanticCheckStatus
    reason: str = Field(min_length=1, max_length=500)


class ClaimSemanticVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^claim-[0-9a-f]{20}$")
    implication: SemanticDimensionCheck
    negation: SemanticDimensionCheck
    unit: SemanticDimensionCheck
    population: SemanticDimensionCheck
    condition: SemanticDimensionCheck
    temporality: SemanticDimensionCheck
    supported: bool

    @model_validator(mode="after")
    def calculate_support_from_all_dimensions(self) -> ClaimSemanticVerification:
        checks = (
            self.negation,
            self.unit,
            self.population,
            self.condition,
            self.temporality,
        )
        expected = self.implication.status == "entailed" and all(
            check.status in {"entailed", "not_applicable"} for check in checks
        )
        if self.supported is not expected:
            raise ValueError("semantic support must be calculated from every required dimension")
        return self


class SemanticVerificationCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    verifications: list[ClaimSemanticVerification] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_claims(self) -> SemanticVerificationCheckpoint:
        claim_ids = [item.claim_id for item in self.verifications]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("semantic claim verification cannot be duplicated")
        return self


class _VerificationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^claim-[0-9a-f]{20}$")
    implication: SemanticDimensionCheck
    negation: SemanticDimensionCheck
    unit: SemanticDimensionCheck
    population: SemanticDimensionCheck
    condition: SemanticDimensionCheck
    temporality: SemanticDimensionCheck


class _VerificationDrafts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verifications: list[_VerificationDraft] = Field(default_factory=list, max_length=20)


class SemanticVerificationClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Any: ...


class SemanticVerificationError(RuntimeError):
    """The model did not return one complete verification per atomic claim."""


_CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["entailed", "contradicted", "uncertain", "not_applicable"],
        },
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["status", "reason"],
    "additionalProperties": False,
}
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verifications": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string"},
                    "implication": _CHECK_SCHEMA,
                    "negation": _CHECK_SCHEMA,
                    "unit": _CHECK_SCHEMA,
                    "population": _CHECK_SCHEMA,
                    "condition": _CHECK_SCHEMA,
                    "temporality": _CHECK_SCHEMA,
                },
                "required": [
                    "claim_id",
                    "implication",
                    "negation",
                    "unit",
                    "population",
                    "condition",
                    "temporality",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verifications"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "Vérifie séparément chaque affirmation contre ses extraits verbatim. Renseigne obligatoirement "
    "les six dimensions : implication globale, négation, unité, population, condition et "
    "temporalité. Utilise entailed seulement si l'extrait implique exactement la dimension, "
    "contradicted s'il la contredit, uncertain s'il ne permet pas de décider, et not_applicable "
    "uniquement si la dimension n'apparaît pas dans l'affirmation. Ne complète rien par "
    "connaissance externe et réponds seulement avec l'objet JSON demandé."
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
            response = self.client.chat(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "question": payload.message,
                                "claims": [
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
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                json_schema=_RESPONSE_SCHEMA,
                temperature=0.0,
                max_output_tokens=4_096,
            )
            try:
                drafts = _VerificationDrafts.model_validate_json(response.content)
                self._validate_coverage(claims.claims, drafts.verifications)
                verifications = [self._materialize(item) for item in drafts.verifications]
            except (TypeError, ValueError) as error:
                raise SemanticVerificationError(
                    "semantic verification is incomplete or references unknown claims"
                ) from error
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
        verifications: list[_VerificationDraft | ClaimSemanticVerification],
    ) -> None:
        expected = {claim.claim_id for claim in claims}
        observed = {item.claim_id for item in verifications}
        if len(observed) != len(verifications) or observed != expected:
            raise ValueError("every and only atomic claim must be verified exactly once")

    @staticmethod
    def _materialize(draft: _VerificationDraft) -> ClaimSemanticVerification:
        secondary = (
            draft.negation,
            draft.unit,
            draft.population,
            draft.condition,
            draft.temporality,
        )
        supported = draft.implication.status == "entailed" and all(
            check.status in {"entailed", "not_applicable"} for check in secondary
        )
        return ClaimSemanticVerification(
            **draft.model_dump(),
            supported=supported,
        )
