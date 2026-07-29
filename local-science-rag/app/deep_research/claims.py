"""Strict atomic-claim extraction from bounded, verbatim local excerpts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.corpora import CorpusScope
from app.deep_research.citations import CitationSourceFragment
from app.jobs.contracts import DeepResearchPayload
from app.models.synthesis import DOI_PATTERN, MODEL_CITATION_PATTERN

MAX_ATOMIC_CLAIMS = 20
MAX_CLAIM_SOURCES = 24
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class AtomicClaimEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: CorpusScope
    article_id: str = Field(min_length=1, max_length=200)
    chunk_id: int = Field(ge=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    source_excerpt: str = Field(min_length=1, max_length=1_200)
    source_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AtomicClaim(BaseModel):
    """One statement with exactly one scientific discourse role."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^claim-[0-9a-f]{20}$")
    statement: str = Field(min_length=1, max_length=2_000)
    role: Literal["result", "interpretation", "recommendation"]
    evidence: list[AtomicClaimEvidence] = Field(min_length=1, max_length=4)

    @field_validator("statement")
    @classmethod
    def forbid_model_generated_references(cls, value: str) -> str:
        if DOI_PATTERN.search(value) or MODEL_CITATION_PATTERN.search(value):
            raise ValueError("atomic claim cannot contain a model-generated reference")
        return value

    @model_validator(mode="after")
    def unique_evidence(self) -> AtomicClaim:
        identities = {
            (
                item.scope,
                item.article_id,
                item.chunk_id,
                item.source_excerpt,
            )
            for item in self.evidence
        }
        if len(identities) != len(self.evidence):
            raise ValueError("atomic claim evidence cannot be duplicated")
        return self


class AtomicClaimCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    question_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_fragment_count: int = Field(ge=0, le=24)
    claims: list[AtomicClaim] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_claims(self) -> AtomicClaimCheckpoint:
        identifiers = [claim.claim_id for claim in self.claims]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("atomic claim identifiers cannot be duplicated")
        return self


class _ClaimDraftEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(pattern=r"^source-[1-9][0-9]*$")
    source_excerpt: str = Field(min_length=1, max_length=1_200)


class _AtomicClaimDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=2_000)
    role: Literal["result", "interpretation", "recommendation"]
    evidence: list[_ClaimDraftEvidence] = Field(min_length=1, max_length=4)


class _AtomicClaimDrafts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[_AtomicClaimDraft] = Field(default_factory=list, max_length=20)


class AtomicClaimClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Any: ...


class AtomicClaimExtractionError(RuntimeError):
    """ARGO returned a claim that cannot be tied to the supplied local excerpts."""


_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "maxItems": MAX_ATOMIC_CLAIMS,
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string", "maxLength": 2000},
                    "role": {
                        "type": "string",
                        "enum": ["result", "interpretation", "recommendation"],
                    },
                    "evidence": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "source_key": {"type": "string"},
                                "source_excerpt": {"type": "string", "maxLength": 1200},
                            },
                            "required": ["source_key", "source_excerpt"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["statement", "role", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "Extrais uniquement des affirmations atomiques répondant à la question depuis les extraits "
    "fournis. Une affirmation doit relever d'un seul rôle : résultat observé, interprétation ou "
    "recommandation. Ne mélange jamais ces rôles. Copie au moins un source_excerpt exactement, "
    "sans le reformuler. N'ajoute aucun fait absent et réponds seulement avec l'objet JSON demandé."
)


def _excerpt_candidates(text: str) -> tuple[str, ...]:
    sentences = [item.strip() for item in _SENTENCE_SPLIT.split(text) if item.strip()]
    if not sentences:
        sentences = [text.strip()]
    return tuple(item[:1_200] for item in sentences[:2] if item[:1_200])


class AtomicClaimExtractionStage:
    def __init__(
        self,
        client: AtomicClaimClient | None,
        checkpoint_root: Path,
    ) -> None:
        self.client = client
        self.checkpoint_root = checkpoint_root

    def _path(self, payload: DeepResearchPayload) -> Path:
        return (
            self.checkpoint_root
            / str(payload.conversation_id)
            / str(payload.client_request_id)
            / "atomic-claims.json"
        )

    def load(self, payload: DeepResearchPayload) -> AtomicClaimCheckpoint:
        path = self._path(payload)
        if not path.is_file():
            raise RuntimeError("deep-research atomic-claim checkpoint is missing")
        return AtomicClaimCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def extract(
        self,
        payload: DeepResearchPayload,
        fragments: list[CitationSourceFragment],
    ) -> AtomicClaimCheckpoint:
        path = self._path(payload)
        if path.is_file():
            return self.load(payload)
        sources = fragments[:MAX_CLAIM_SOURCES]
        source_map = {
            f"source-{index}": fragment for index, fragment in enumerate(sources, start=1)
        }
        allowed_excerpts = {
            key: _excerpt_candidates(fragment.text) for key, fragment in source_map.items()
        }
        claims: list[AtomicClaim] = []
        if self.client is not None and source_map:
            response = self.client.chat(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "question": payload.message,
                                "sources": [
                                    {
                                        "source_key": key,
                                        "allowed_excerpts": allowed_excerpts[key],
                                    }
                                    for key in source_map
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                json_schema=_DRAFT_SCHEMA,
                temperature=0.0,
                max_output_tokens=2_048,
            )
            try:
                drafts = _AtomicClaimDrafts.model_validate_json(response.content)
                claims = self._materialize(drafts, source_map, allowed_excerpts)
            except (TypeError, ValueError) as error:
                raise AtomicClaimExtractionError(
                    "atomic claims do not match the supplied local excerpts"
                ) from error
        checkpoint = AtomicClaimCheckpoint(
            question_sha256=hashlib.sha256(payload.message.encode()).hexdigest(),
            source_fragment_count=len(sources),
            claims=claims,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return checkpoint

    @staticmethod
    def _materialize(
        drafts: _AtomicClaimDrafts,
        source_map: dict[str, CitationSourceFragment],
        allowed_excerpts: dict[str, tuple[str, ...]],
    ) -> list[AtomicClaim]:
        materialized: list[AtomicClaim] = []
        for draft in drafts.claims:
            evidence: list[AtomicClaimEvidence] = []
            for item in draft.evidence:
                fragment = source_map.get(item.source_key)
                if (
                    fragment is None
                    or item.source_excerpt not in allowed_excerpts[item.source_key]
                    or item.source_excerpt not in fragment.text
                ):
                    raise ValueError("atomic claim excerpt is not verbatim local text")
                evidence.append(
                    AtomicClaimEvidence(
                        scope=fragment.scope,
                        article_id=fragment.article_id,
                        chunk_id=fragment.chunk_id,
                        page_start=fragment.page_start,
                        page_end=fragment.page_end,
                        source_excerpt=item.source_excerpt,
                        source_text_sha256=hashlib.sha256(fragment.text.encode()).hexdigest(),
                    )
                )
            identity = json.dumps(
                {
                    "statement": draft.statement,
                    "role": draft.role,
                    "evidence": [
                        (item.scope, item.article_id, item.chunk_id, item.source_excerpt)
                        for item in evidence
                    ],
                },
                ensure_ascii=True,
                sort_keys=True,
            )
            materialized.append(
                AtomicClaim(
                    claim_id=f"claim-{hashlib.sha256(identity.encode()).hexdigest()[:20]}",
                    statement=draft.statement,
                    role=draft.role,
                    evidence=evidence,
                )
            )
        return materialized
