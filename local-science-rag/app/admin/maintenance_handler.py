"""Checkpointed durable handler for explicit administrator weekly maintenance."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.admin.corpus_backup import MaintenanceBackup
from app.admin.maintenance_operations import (
    MaintenanceOperationResult,
    MaintenancePublication,
    ProductionMaintenanceOperations,
)
from app.admin.maintenance_state import record_success
from app.config import Settings
from app.corpora import LocalProfile
from app.jobs.contracts import JobStep, JobType, WeeklyMaintenancePayload
from app.jobs.repository import JobRecord
from app.jobs.worker import JobHandlerResult, JobProgressContext


class MaintenanceOperations(Protocol):
    def backup(self) -> MaintenanceBackup: ...
    def suggestions(self) -> MaintenanceOperationResult: ...
    def harvest(self) -> MaintenanceOperationResult: ...
    def index(self) -> MaintenanceOperationResult: ...
    def validate(self) -> MaintenanceOperationResult: ...
    def publish(self) -> MaintenancePublication: ...
    def rollback(self, backup: MaintenanceBackup) -> None: ...


class MaintenanceCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maintenance_id: str
    completed_steps: list[JobStep] = Field(default_factory=list)
    backup: MaintenanceBackup | None = None
    counters: dict[str, int] = Field(default_factory=dict)
    details: dict[str, str] = Field(default_factory=dict)
    publication: MaintenancePublication | None = None


class MaintenanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maintenance_id: str
    state: str
    completed_at: datetime
    corpus_version: str | None
    counters: dict[str, int]
    details: dict[str, str]
    errors: list[str] = Field(default_factory=list, max_length=20)


class WeeklyMaintenanceHandler:
    def __init__(
        self,
        settings: Settings,
        *,
        operations_factory=None,
    ) -> None:
        self.settings = settings
        self.operations_factory = operations_factory or ProductionMaintenanceOperations

    def _root(self, payload: WeeklyMaintenancePayload) -> Path:
        return self.settings.paths.data_dir / "admin" / "maintenance" / str(payload.maintenance_id)

    def _checkpoint_path(self, payload: WeeklyMaintenancePayload) -> Path:
        return self._root(payload) / "checkpoint.json"

    def _load_checkpoint(self, payload: WeeklyMaintenancePayload) -> MaintenanceCheckpoint:
        path = self._checkpoint_path(payload)
        if not path.is_file():
            return MaintenanceCheckpoint(maintenance_id=str(payload.maintenance_id))
        return MaintenanceCheckpoint.model_validate_json(path.read_bytes())

    def _save_checkpoint(
        self,
        payload: WeeklyMaintenancePayload,
        checkpoint: MaintenanceCheckpoint,
    ) -> None:
        destination = self._checkpoint_path(payload)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".checkpoint.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(checkpoint.model_dump_json(indent=2) + "\n", encoding="utf-8")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_report(self, payload: WeeklyMaintenancePayload, report: MaintenanceReport) -> None:
        destination = self._root(payload) / "report.json"
        temporary = destination.with_name(f".report.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _merge(checkpoint: MaintenanceCheckpoint, result: MaintenanceOperationResult) -> None:
        checkpoint.counters.update(result.counters)
        checkpoint.details.update(result.details)

    def handle(self, job: JobRecord, context: JobProgressContext) -> JobHandlerResult:
        if job.type is not JobType.WEEKLY_MAINTENANCE or not isinstance(
            job.payload, WeeklyMaintenancePayload
        ):
            raise ValueError("weekly maintenance handler received a different job type")
        started = perf_counter()
        payload = job.payload
        checkpoint = self._load_checkpoint(payload)
        operations = self.operations_factory(self.settings, payload.maintenance_id)
        try:
            if JobStep.BACKUP not in checkpoint.completed_steps:
                context.publish(JobStep.BACKUP)
                context.check_cancellation()
                checkpoint.backup = operations.backup()
                checkpoint.completed_steps.append(JobStep.BACKUP)
                self._save_checkpoint(payload, checkpoint)
            for step, operation in (
                (JobStep.SUGGESTIONS, operations.suggestions),
                (JobStep.HARVEST, operations.harvest),
                (JobStep.INDEX, operations.index),
                (JobStep.VALIDATION, operations.validate),
            ):
                if step in checkpoint.completed_steps:
                    continue
                context.publish(step)
                context.check_cancellation()
                self._merge(checkpoint, operation())
                checkpoint.completed_steps.append(step)
                self._save_checkpoint(payload, checkpoint)
            if JobStep.PUBLISH not in checkpoint.completed_steps:
                context.publish(JobStep.PUBLISH)
                context.check_cancellation()
                checkpoint.publication = operations.publish()
                checkpoint.completed_steps.append(JobStep.PUBLISH)
                self._save_checkpoint(payload, checkpoint)
            context.publish(JobStep.PERSISTENCE)
            assert checkpoint.publication is not None
            report = MaintenanceReport(
                maintenance_id=str(payload.maintenance_id),
                state="published",
                completed_at=datetime.now(UTC),
                corpus_version=checkpoint.publication.corpus_version,
                counters=checkpoint.counters,
                details=checkpoint.details,
            )
            self._write_report(payload, report)
            record_success(
                self.settings,
                LocalProfile.ADMIN,
                corpus_version=checkpoint.publication.corpus_version,
                job_id=job.id,
                completed_at=report.completed_at,
            )
        except Exception as exc:
            if checkpoint.backup is not None and JobStep.PUBLISH not in checkpoint.completed_steps:
                operations.rollback(checkpoint.backup)
                checkpoint.completed_steps = [JobStep.BACKUP]
                self._save_checkpoint(payload, checkpoint)
                context.repository.rewind_maintenance_after_rollback(
                    job.id,
                    worker_id=context.worker_id,
                    now=context.clock(),
                )
            self._write_report(
                payload,
                MaintenanceReport(
                    maintenance_id=str(payload.maintenance_id),
                    state="failed",
                    completed_at=datetime.now(UTC),
                    corpus_version=None,
                    counters=checkpoint.counters,
                    details=checkpoint.details,
                    errors=[type(exc).__name__],
                ),
            )
            raise
        return JobHandlerResult(
            assistant_content="Maintenance hebdomadaire publiée.",
            assistant_response=report.model_dump(mode="json"),
            response_time_milliseconds=(perf_counter() - started) * 1000,
        )
