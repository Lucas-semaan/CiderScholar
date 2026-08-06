"""Sequential, resumable execution of prompt-profile evaluation campaigns."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import monotonic, sleep
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.evaluation.chat_finetuning import EvaluationRunAudit, audit_evaluation_run
from app.jobs.contracts import ACTIVE_JOB_STATES, JobErrorKind, JobState
from app.jobs.repository import (
    EvaluationQuestionAlreadySubmittedError,
    EvaluationRunBusyError,
    JobRecord,
    JobRepository,
)


class EvaluationCellSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    profile: Literal["p0", "p1", "p2"]
    message: str = Field(min_length=2, max_length=4000)

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        return " ".join(value.split())

    @property
    def key(self) -> str:
        return f"{self.profile}:{self.question_id}"


class EvaluationCampaignSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    cells: list[EvaluationCellSpec] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_cells(self) -> EvaluationCampaignSpec:
        keys = [cell.key for cell in self.cells]
        if len(set(keys)) != len(keys):
            raise ValueError("evaluation campaign cells must be unique")
        return self

    def fingerprint(self) -> str:
        canonical = self.model_dump_json(exclude_none=True)
        return sha256(canonical.encode("utf-8")).hexdigest()


class EvaluationCampaignResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    expected_cells: int = Field(ge=1)
    terminal_cells: int = Field(ge=0)
    succeeded_cells: int = Field(ge=0)
    stopped_early: bool
    complete: bool
    reliable: bool
    audit: EvaluationRunAudit


class CampaignExecutionError(RuntimeError):
    """The campaign cannot safely continue without losing a cell outcome."""


class EvaluationCampaignRunner:
    """Submit one immutable cell at a time and require a visible terminal outcome."""

    def __init__(
        self,
        repository: JobRepository,
        output_dir: str | Path,
        *,
        poll_seconds: float = 1.0,
        job_timeout_seconds: float = 3600.0,
        cancellation_grace_seconds: float = 300.0,
        sleeper: Callable[[float], None] = sleep,
        monotonic_clock: Callable[[], float] = monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_poll: Callable[[], None] | None = None,
    ) -> None:
        if poll_seconds < 0:
            raise ValueError("poll_seconds cannot be negative")
        if job_timeout_seconds <= 0 or cancellation_grace_seconds <= 0:
            raise ValueError("campaign timeouts must be positive")
        self.repository = repository
        self.output_dir = Path(output_dir)
        self.poll_seconds = poll_seconds
        self.job_timeout_seconds = job_timeout_seconds
        self.cancellation_grace_seconds = cancellation_grace_seconds
        self.sleeper = sleeper
        self.monotonic = monotonic_clock
        self.utc_now = utc_now
        self.on_poll = on_poll
        self.state_path = self.output_dir / "state.json"
        self.events_path = self.output_dir / "events.jsonl"
        self.audit_path = self.output_dir / "audit.json"
        self.report_path = self.output_dir / "report.md"

    def run(self, spec: EvaluationCampaignSpec) -> EvaluationCampaignResult:
        self.repository.initialize()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        state = self._load_or_create_state(spec)
        self._event("campaign_resumed" if state["cells"] else "campaign_started", spec.run_id)
        stopped_early = False
        try:
            for cell in spec.cells:
                cell_state = state["cells"].get(cell.key, {})
                if cell_state.get("state") in {
                    JobState.SUCCEEDED.value,
                    JobState.FAILED.value,
                    JobState.CANCELLED.value,
                }:
                    continue
                job = self._restore_or_submit(spec.run_id, cell, cell_state)
                state["cells"][cell.key] = {
                    "question_id": cell.question_id,
                    "profile": cell.profile,
                    "question_sha256": sha256(cell.message.encode("utf-8")).hexdigest(),
                    "job_id": str(job.id),
                    "conversation_id": str(job.conversation_id),
                    "state": job.state.value,
                }
                self._save_state(state)
                terminal = self._wait_for_terminal(job)
                visible = self._visible_outcome(terminal)
                state["cells"][cell.key].update(visible)
                state["cells"][cell.key]["state"] = terminal.state.value
                self._save_state(state)
                self._event(
                    "cell_terminal",
                    spec.run_id,
                    cell=cell,
                    job=terminal,
                    outcome=visible,
                )
                if terminal.state is not JobState.SUCCEEDED:
                    stopped_early = cell.key != spec.cells[-1].key
                    break
        except Exception as exc:
            safe_diagnostic = (
                " ".join(str(exc).split())[:300]
                if isinstance(exc, CampaignExecutionError)
                else type(exc).__name__
            )
            state["status"] = "stopped"
            state["stop_reason"] = type(exc).__name__
            state["stop_diagnostic"] = safe_diagnostic
            self._save_state(state)
            self._event("campaign_stopped", spec.run_id, diagnostic=safe_diagnostic)
            self._write_report(spec, state, result=None)
            raise

        audit = audit_evaluation_run(self.repository.path, spec.run_id)
        terminal_cells = sum(
            cell.get("state")
            in {JobState.SUCCEEDED.value, JobState.FAILED.value, JobState.CANCELLED.value}
            for cell in state["cells"].values()
        )
        succeeded_cells = sum(
            cell.get("state") == JobState.SUCCEEDED.value for cell in state["cells"].values()
        )
        complete = terminal_cells == len(spec.cells)
        result = EvaluationCampaignResult(
            run_id=spec.run_id,
            expected_cells=len(spec.cells),
            terminal_cells=terminal_cells,
            succeeded_cells=succeeded_cells,
            stopped_early=stopped_early,
            complete=complete,
            reliable=complete and audit.reliable,
            audit=audit,
        )
        state["status"] = "completed" if complete else "stopped"
        state["stop_reason"] = None if complete else "terminal_failure"
        self._save_state(state)
        self._atomic_write(self.audit_path, audit.model_dump_json(indent=2) + "\n")
        self._write_report(spec, state, result=result)
        self._event("campaign_completed", spec.run_id, result=result)
        return result

    def _restore_or_submit(
        self,
        run_id: str,
        cell: EvaluationCellSpec,
        cell_state: dict[str, object],
    ) -> JobRecord:
        existing_id = cell_state.get("job_id")
        if isinstance(existing_id, str):
            existing = self.repository.get(UUID(existing_id))
            if existing is None:
                raise CampaignExecutionError("persisted campaign job no longer exists")
            return existing
        client_request_id = uuid5(NAMESPACE_URL, f"ciderscholar:{run_id}:{cell.key}")
        try:
            enqueued = self.repository.enqueue_evaluation_question(
                run_id=run_id,
                question_id=cell.question_id,
                profile=cell.profile,
                message=cell.message,
                client_request_id=client_request_id,
            )
        except EvaluationRunBusyError as exc:
            raise CampaignExecutionError(
                "the durable queue is not idle; no evaluation question was submitted"
            ) from exc
        except EvaluationQuestionAlreadySubmittedError as exc:
            raise CampaignExecutionError(
                "the evaluation cell already exists with another request identity"
            ) from exc
        self._event("cell_submitted", run_id, cell=cell, job=enqueued.job)
        return enqueued.job

    def _wait_for_terminal(self, initial: JobRecord) -> JobRecord:
        started = self.monotonic()
        cancellation_started: float | None = None
        quota_retry_at: datetime | None = None
        job = initial
        while job.state in ACTIVE_JOB_STATES:
            if self.on_poll is not None:
                self.on_poll()
            refreshed = self.repository.get(job.id)
            if refreshed is None:
                raise CampaignExecutionError("submitted evaluation job disappeared")
            job = refreshed
            if job.state not in ACTIVE_JOB_STATES:
                break
            now = self.utc_now()
            if (
                job.state is JobState.QUEUED
                and job.error_code is JobErrorKind.QUOTA
                and job.available_at > now
            ):
                quota_retry_at = job.available_at
                self.sleeper(self.poll_seconds)
                continue
            if quota_retry_at is not None and now >= quota_retry_at:
                started = self.monotonic()
                quota_retry_at = None
            elapsed = self.monotonic() - started
            if elapsed >= self.job_timeout_seconds and cancellation_started is None:
                if job.state is JobState.QUEUED:
                    job = self.repository.cancel_queued(job.id) or job
                elif job.state is JobState.RUNNING:
                    job = self.repository.request_cancellation(job.id) or job
                cancellation_started = self.monotonic()
                self._event("cell_timeout_cancellation_requested", None, job=job)
            if cancellation_started is not None:
                self.repository.recover_expired_leases(now=now)
                if self.monotonic() - cancellation_started >= self.cancellation_grace_seconds:
                    refreshed = self.repository.get(job.id)
                    if refreshed is not None and refreshed.state in ACTIVE_JOB_STATES:
                        raise CampaignExecutionError(
                            "evaluation job did not reach a terminal state after cancellation"
                        )
            if job.state in ACTIVE_JOB_STATES:
                self.sleeper(self.poll_seconds)
        return job

    def _visible_outcome(self, job: JobRecord) -> dict[str, object]:
        if job.result_message_id is None:
            raise CampaignExecutionError("terminal evaluation job has no result message")
        conversation = self.repository.database.chat_conversation(str(job.conversation_id))
        if conversation is None:
            raise CampaignExecutionError("terminal evaluation conversation disappeared")
        result = next(
            (
                message
                for message in conversation["messages"]
                if message["id"] == str(job.result_message_id)
            ),
            None,
        )
        if result is None or result["role"] != "assistant" or not result["content"].strip():
            raise CampaignExecutionError("terminal evaluation outcome is not visible")
        response = result.get("response")
        if not isinstance(response, dict):
            raise CampaignExecutionError("terminal evaluation outcome is not structured")
        if job.state is JobState.SUCCEEDED:
            trace = response.get("evaluation")
            if not isinstance(trace, dict):
                raise CampaignExecutionError("successful evaluation response has no trace")
            return {
                "generation_status": response.get("generation_status", "generated"),
                "diagnostic_code": response.get("diagnostic_code"),
                "result_message_id": str(job.result_message_id),
            }
        if (
            response.get("kind") != "job_terminal_notice"
            or response.get("job_id") != str(job.id)
            or response.get("state") != job.state.value
        ):
            raise CampaignExecutionError("terminal evaluation notice identity mismatch")
        return {
            "generation_status": "terminal_notice",
            "diagnostic_code": response.get("diagnostic_code"),
            "result_message_id": str(job.result_message_id),
        }

    def _load_or_create_state(self, spec: EvaluationCampaignSpec) -> dict[str, object]:
        fingerprint = spec.fingerprint()
        if self.state_path.is_file():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("run_id") != spec.run_id or state.get("manifest_sha256") != fingerprint:
                raise CampaignExecutionError(
                    "existing campaign state does not match the immutable manifest"
                )
            if state.get("status") == "stopped" and state.get("stop_reason") == "terminal_failure":
                raise CampaignExecutionError(
                    "a campaign stopped by a terminal failure requires a new run_id"
                )
            return state
        now = self.utc_now().isoformat()
        state: dict[str, object] = {
            "run_id": spec.run_id,
            "manifest_sha256": fingerprint,
            "status": "running",
            "started_at": now,
            "updated_at": now,
            "stop_reason": None,
            "stop_diagnostic": None,
            "cells": {},
        }
        self._save_state(state)
        return state

    def _save_state(self, state: dict[str, object]) -> None:
        state["updated_at"] = self.utc_now().isoformat()
        self._atomic_write(
            self.state_path,
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def _event(
        self,
        event: str,
        run_id: str | None,
        *,
        cell: EvaluationCellSpec | None = None,
        job: JobRecord | None = None,
        outcome: dict[str, object] | None = None,
        result: EvaluationCampaignResult | None = None,
        diagnostic: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "at": self.utc_now().isoformat(),
            "event": event,
            "run_id": run_id,
        }
        if cell is not None:
            payload.update(
                {
                    "cell": cell.key,
                    "question_sha256": sha256(cell.message.encode("utf-8")).hexdigest(),
                }
            )
        if job is not None:
            payload.update({"job_id": str(job.id), "job_state": job.state.value})
        if outcome is not None:
            payload["outcome"] = outcome
        if result is not None:
            payload.update(
                {
                    "complete": result.complete,
                    "reliable": result.reliable,
                    "terminal_cells": result.terminal_cells,
                }
            )
        if diagnostic is not None:
            payload["diagnostic"] = diagnostic
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def _write_report(
        self,
        spec: EvaluationCampaignSpec,
        state: dict[str, object],
        *,
        result: EvaluationCampaignResult | None,
    ) -> None:
        cells = state.get("cells", {})
        lines = [
            f"# Campagne {spec.run_id}",
            "",
            f"- Statut : `{state.get('status')}`",
            f"- Cellules attendues : {len(spec.cells)}",
            f"- Cellules enregistrées : {len(cells) if isinstance(cells, dict) else 0}",
        ]
        if state.get("stop_diagnostic"):
            lines.append(f"- Diagnostic d'arrêt : `{state['stop_diagnostic']}`")
        if result is not None:
            lines.extend(
                [
                    f"- Complète : `{str(result.complete).lower()}`",
                    f"- Fiable : `{str(result.reliable).lower()}`",
                    f"- Issues : `{json.dumps(result.audit.outcome_counts, sort_keys=True)}`",
                ]
            )
        lines.extend(
            ["", "## Cellules", "", "| Cellule | État | Sortie | Diagnostic |", "|---|---|---|---|"]
        )
        if isinstance(cells, dict):
            for key, raw in cells.items():
                cell = raw if isinstance(raw, dict) else {}
                lines.append(
                    f"| {key} | {cell.get('state', 'unknown')} | "
                    f"{cell.get('generation_status', 'pending')} | "
                    f"{cell.get('diagnostic_code') or ''} |"
                )
        self._atomic_write(self.report_path, "\n".join(lines) + "\n")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
