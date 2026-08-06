"""Durable handlers for long synthesis and corpus ingestion."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

from app.config import Settings
from app.database.sqlite import Database
from app.ingestion.pipeline import IngestionReport
from app.jobs.contracts import (
    CorpusIngestionPayload,
    JobStep,
    JobType,
    LongSynthesisPayload,
)
from app.jobs.repository import JobRecord
from app.jobs.worker import JobHandlerResult, JobProgressContext
from app.services.workflows import ingest_paths, synthesize_query


class SynthesisRunner(Protocol):
    def __call__(
        self,
        settings: Settings,
        database: Database,
        *,
        query_id: str,
        resume: bool,
    ) -> Any: ...


class CorpusIngester(Protocol):
    def __call__(
        self,
        settings: Settings,
        database: Database,
        paths: Sequence[Path],
        *,
        progress: Callable[[int, int, str, str], None] | None = None,
    ) -> list[IngestionReport]: ...


@dataclass(slots=True)
class LongSynthesisHandler:
    settings: Settings
    database: Database
    synthesize: SynthesisRunner = synthesize_query
    clock: Callable[[], float] = monotonic

    def handle(self, job: JobRecord, context: JobProgressContext) -> JobHandlerResult:
        if job.type is not JobType.LONG_SYNTHESIS or not isinstance(
            job.payload, LongSynthesisPayload
        ):
            raise ValueError("long-synthesis handler received another job type")
        started_at = self.clock()
        context.check_cancellation()
        context.publish(JobStep.SYNTHESIS, technical_message="synthesis.started")
        execution = self.synthesize(
            self.settings,
            self.database,
            query_id=job.payload.query_id,
            resume=job.payload.resume,
        )
        context.check_cancellation()
        return JobHandlerResult(
            assistant_content=execution.result.answer_markdown,
            assistant_response=execution.model_dump(mode="json"),
            response_time_milliseconds=max(0.0, (self.clock() - started_at) * 1000),
        )


@dataclass(slots=True)
class CorpusIngestionHandler:
    settings: Settings
    database: Database
    ingest: CorpusIngester = ingest_paths
    clock: Callable[[], float] = monotonic

    def handle(self, job: JobRecord, context: JobProgressContext) -> JobHandlerResult:
        if job.type is not JobType.CORPUS_INGESTION or not isinstance(
            job.payload, CorpusIngestionPayload
        ):
            raise ValueError("corpus-ingestion handler received another job type")
        started_at = self.clock()
        root = self.settings.paths.pdf_dir.resolve()
        paths = [self._resolve_staged_pdf(root, item) for item in job.payload.staged_files]
        context.check_cancellation()
        context.publish(JobStep.INGESTION, technical_message="corpus_ingestion.started")

        def progress(_completed: int, _total: int, _name: str, _state: str) -> None:
            context.check_cancellation()

        reports = self.ingest(
            self.settings,
            self.database,
            paths,
            progress=progress,
        )
        counts = {
            status: sum(report.status == status for report in reports)
            for status in ("chunks_ready", "duplicate", "ocr_required", "failed")
        }
        context.check_cancellation()
        return JobHandlerResult(
            assistant_content=(
                f"Ingestion terminée : {len(reports)} document(s), "
                f"{counts['chunks_ready']} prêt(s), {counts['duplicate']} doublon(s), "
                f"{counts['ocr_required']} à OCRiser, {counts['failed']} en échec."
            ),
            assistant_response={"document_count": len(reports), "status_counts": counts},
            response_time_milliseconds=max(0.0, (self.clock() - started_at) * 1000),
        )

    @staticmethod
    def _resolve_staged_pdf(root: Path, file_name: str) -> Path:
        candidate = (root / file_name).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("staged PDF escapes its configured directory") from error
        if not candidate.is_file() or candidate.suffix.casefold() != ".pdf":
            raise FileNotFoundError("a staged PDF is unavailable")
        return candidate
