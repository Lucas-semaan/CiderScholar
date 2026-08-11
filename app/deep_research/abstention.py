"""Deterministic abstention when no verified claim remains."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.deep_research.admission import ClaimAdmissionCheckpoint
from app.deep_research.iteration import ResearchLoopCheckpoint
from app.jobs.contracts import DeepResearchPayload
from app.llm.response_language import question_language, validate_output_language


def _default_gap(language: Literal["fr", "en"]) -> str:
    if language == "fr":
        return "Aucune affirmation locale n’a franchi tous les contrôles sémantiques."
    return "No local claim passed all semantic checks."


class DeepResearchReadinessCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
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
            / "readiness-v2.json"
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
            language = question_language(payload.message)
            gaps: list[str] = []
            for record in loop.iterations:
                if record.gap is None:
                    continue
                try:
                    validate_output_language(payload.message, [record.gap.description])
                except RuntimeError:
                    continue
                gaps.append(record.gap.description)
            gaps = gaps or [_default_gap(language)]
            unique_gaps = list(dict.fromkeys(gaps))[:2]
            rendered = "\n".join(f"- {gap}" for gap in unique_gaps)
            introduction = (
                "Je ne peux pas répondre de façon étayée avec les preuves locales disponibles."
                if language == "fr"
                else "I cannot provide a grounded answer from the available local evidence."
            )
            heading = "Lacunes constatées" if language == "fr" else "Identified evidence gaps"
            checkpoint = DeepResearchReadinessCheckpoint(
                outcome="abstain",
                admitted_claim_count=0,
                gap_descriptions=unique_gaps,
                abstention_markdown=f"{introduction}\n\n{heading}:\n{rendered}",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return checkpoint
