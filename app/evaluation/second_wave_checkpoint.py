"""Content-addressed checkpoints for the evaluation-only second-wave harness."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.evaluation.evidence_gaps import EvidenceGapPlan
from app.evaluation.second_wave import SecondWaveResult
from app.retrieval.chat_checkpoint import ChatCheckpointEvidence

CheckpointStage = Literal["first_wave", "gaps", "second_wave", "complete"]


class SecondWaveCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieval_version: str = Field(min_length=1, max_length=200)
    first_wave: tuple[ChatCheckpointEvidence, ...] = Field(default=(), max_length=48)
    gaps: EvidenceGapPlan | None = None
    second_wave: SecondWaveResult | None = None
    checkpoint_sha256: str = Field(default="", pattern=r"^(|[0-9a-f]{64})$")

    def next_stage(self) -> CheckpointStage:
        if not self.first_wave:
            return "first_wave"
        if self.gaps is None:
            return "gaps"
        if self.second_wave is None:
            return "second_wave"
        return "complete"


def seal_checkpoint(checkpoint: SecondWaveCheckpoint) -> SecondWaveCheckpoint:
    payload = checkpoint.model_dump(mode="json", exclude={"checkpoint_sha256"})
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return checkpoint.model_copy(update={"checkpoint_sha256": digest})


def checkpoint_is_current(
    checkpoint: SecondWaveCheckpoint,
    *,
    corpus_sha256: str,
    retrieval_version: str,
) -> bool:
    return (
        checkpoint.corpus_sha256 == corpus_sha256
        and checkpoint.retrieval_version == retrieval_version
        and checkpoint.checkpoint_sha256 == seal_checkpoint(checkpoint).checkpoint_sha256
    )
