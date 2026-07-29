"""Checkpointed durable orchestration for the deep-research pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.jobs.contracts import JOB_STEP_ORDER, DeepResearchPayload, JobStep, JobType
from app.jobs.repository import JobRecord
from app.jobs.worker import JobHandlerResult, JobProgressContext


class DeepResearchOperations(Protocol):
    def search(self, payload: DeepResearchPayload) -> None: ...

    def confirm_reranking(self, payload: DeepResearchPayload) -> None: ...

    def extract_evidence(self, payload: DeepResearchPayload) -> None: ...

    def verify(self, payload: DeepResearchPayload) -> None: ...

    def synthesize(self, payload: DeepResearchPayload) -> str: ...


class DeepResearchCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed_steps: list[JobStep] = Field(default_factory=list)
    answer_markdown: str | None = None


@dataclass(slots=True)
class DeepResearchHandler:
    checkpoint_root: Path
    operations: DeepResearchOperations

    def _path(self, job: JobRecord) -> Path:
        return self.checkpoint_root / str(job.id) / "checkpoint.json"

    def _load(self, job: JobRecord) -> DeepResearchCheckpoint:
        path = self._path(job)
        if not path.is_file():
            return DeepResearchCheckpoint()
        return DeepResearchCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def _save(self, job: JobRecord, checkpoint: DeepResearchCheckpoint) -> None:
        path = self._path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(checkpoint.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _run_step(
        self,
        job: JobRecord,
        context: JobProgressContext,
        checkpoint: DeepResearchCheckpoint,
        step: JobStep,
    ) -> None:
        if step in checkpoint.completed_steps:
            return
        context.check_cancellation()
        if JOB_STEP_ORDER[step] > JOB_STEP_ORDER[job.step]:
            context.publish(step)
        operation = {
            JobStep.SEARCH: self.operations.search,
            JobStep.RERANKING: self.operations.confirm_reranking,
            JobStep.EVIDENCE: self.operations.extract_evidence,
            JobStep.VERIFICATION: self.operations.verify,
        }[step]
        operation(job.payload)
        checkpoint.completed_steps.append(step)
        self._save(job, checkpoint)

    def handle(self, job: JobRecord, context: JobProgressContext) -> JobHandlerResult:
        if job.type is not JobType.DEEP_RESEARCH or not isinstance(
            job.payload, DeepResearchPayload
        ):
            raise ValueError("deep-research handler received another job type")
        checkpoint = self._load(job)
        for step in (
            JobStep.SEARCH,
            JobStep.RERANKING,
            JobStep.EVIDENCE,
            JobStep.VERIFICATION,
        ):
            self._run_step(job, context, checkpoint, step)
        if JobStep.SYNTHESIS not in checkpoint.completed_steps:
            context.check_cancellation()
            if JOB_STEP_ORDER[JobStep.SYNTHESIS] > JOB_STEP_ORDER[job.step]:
                context.publish(JobStep.SYNTHESIS)
            checkpoint.answer_markdown = self.operations.synthesize(job.payload)
            checkpoint.completed_steps.append(JobStep.SYNTHESIS)
            self._save(job, checkpoint)
        if checkpoint.answer_markdown is None:
            raise RuntimeError("deep-research synthesis checkpoint is incomplete")
        details_provider = getattr(self.operations, "response_details", None)
        details = details_provider(job.payload) if callable(details_provider) else {}
        return JobHandlerResult(
            assistant_content=checkpoint.answer_markdown,
            assistant_response={
                "mode": "deep_research",
                "answer_markdown": checkpoint.answer_markdown,
                "details": details,
            },
            response_time_milliseconds=0,
        )

    def close(self) -> None:
        """Release optional model resources owned by the operation pipeline."""

        close = getattr(self.operations, "close", None)
        if callable(close):
            close()
