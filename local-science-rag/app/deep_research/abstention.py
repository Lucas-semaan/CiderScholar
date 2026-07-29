"""Deterministic abstention when no verified claim remains."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.deep_research.admission import ClaimAdmissionCheckpoint
from app.deep_research.iteration import ResearchLoopCheckpoint
from app.jobs.contracts import DeepResearchPayload

_DEFAULT_GAP = "Aucune affirmation locale n’a franchi tous les contrôles sémantiques."


class DeepResearchReadinessCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    outcome: Literal["answerable", "abstain"]
    admitted_claim_count: int = Field(ge=0, le=20)
    gap_descriptions: list[str] = Field(default_factory=list, min_length=0, max_length=2)
    abstention_markdown: str | None = Field(default=None, max_length=3_000)

    @model_validator(mode="after")
    def enforce_fail_closed_outcome(self) -> DeepResearchReadinessCheckpoint:
        if self.outcome == "abstain":
            if self.admitted_claim_count != 0 or not self.gap_descriptions:
                raise ValueError("abstention requires zero admitted claims and an explicit gap")
            if not self.abstention_markdown:
                raise ValueError("abstention requires deterministic user-facing text")
        elif self.admitted_claim_count < 1 or self.abstention_markdown is not None:
            raise ValueError("answerable outcome requires admitted claims and no abstention text")
        return self


class DeepResearchAbstentionStage:
    def __init__(self, checkpoint_root: Path) -> None:
        self.checkpoint_root = checkpoint_root

    def _path(self, payload: DeepResearchPayload) -> Path:
        return (
            self.checkpoint_root
            / str(payload.conversation_id)
            / str(payload.client_request_id)
            / "readiness.json"
        )

    def load(self, payload: DeepResearchPayload) -> DeepResearchReadinessCheckpoint:
        path = self._path(payload)
        if not path.is_file():
            raise RuntimeError("deep-research readiness checkpoint is missing")
        return DeepResearchReadinessCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def decide(
        self,
        payload: DeepResearchPayload,
        loop: ResearchLoopCheckpoint,
        admission: ClaimAdmissionCheckpoint,
    ) -> DeepResearchReadinessCheckpoint:
        path = self._path(payload)
        if path.is_file():
            return self.load(payload)
        admitted_count = sum(item.status == "accepted" for item in admission.decisions)
        if admitted_count:
            checkpoint = DeepResearchReadinessCheckpoint(
                outcome="answerable",
                admitted_claim_count=admitted_count,
            )
        else:
            gaps = [
                record.gap.description for record in loop.iterations if record.gap is not None
            ] or [_DEFAULT_GAP]
            unique_gaps = list(dict.fromkeys(gaps))[:2]
            rendered = "\n".join(f"- {gap}" for gap in unique_gaps)
            checkpoint = DeepResearchReadinessCheckpoint(
                outcome="abstain",
                admitted_claim_count=0,
                gap_descriptions=unique_gaps,
                abstention_markdown=(
                    "Je ne peux pas répondre de façon étayée avec les preuves locales "
                    f"disponibles.\n\nLacunes constatées :\n{rendered}"
                ),
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return checkpoint
