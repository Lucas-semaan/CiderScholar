"""Durable additive watch handler, independent of maintenance publication and purge."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from time import monotonic

from app.config import Settings
from app.corpora import CorpusScope, LocalProfile, load_local_profile, settings_for_corpus
from app.jobs.contracts import JobStep
from app.jobs.repository import JobRecord
from app.jobs.worker import JobCancelledError, JobHandlerResult, JobProgressContext
from app.resource_lock import ResourceFileLock
from app.updates.watch_content import WatchContent
from app.updates.watch_harvest import WatchHarvester
from app.updates.watch_models import WatchCheckpoint, WatchReport
from app.updates.watch_store import WatchStore


class BibliographicWatchHandler:
    def __init__(
        self,
        settings: Settings,
        *,
        content_factory=WatchContent,
        harvester_factory=WatchHarvester,
        clock=monotonic,
    ) -> None:
        self.settings = settings_for_corpus(settings, CorpusScope.COMMON)
        self.store = WatchStore(settings)
        self.content_factory = content_factory
        self.harvester_factory = harvester_factory
        self.clock = clock

    def _backup(self, checkpoint: WatchCheckpoint) -> None:
        directory = self.settings.paths.common_dir / "database" / "watch-backups"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{checkpoint.report.job_id}.sqlite3"
        with (
            closing(self.store.database.connect()) as source,
            closing(sqlite3.connect(target)) as copy,
        ):
            source.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("watch backup integrity check failed")
        checkpoint.backup_done = True
        self.store.save(checkpoint)

    def handle(self, job: JobRecord, context: JobProgressContext) -> JobHandlerResult:
        """Run the administrator watch only when online, then persist its resumable outcome."""

        if load_local_profile() is not LocalProfile.ADMIN:
            raise PermissionError("administrator watch required")
        if self.settings.app.offline_mode:
            raise PermissionError("bibliographic watch is suspended in offline mode")
        started = self.clock()
        checkpoint = self.store.checkpoint(str(job.id))
        if checkpoint is None:
            checkpoint = WatchCheckpoint(
                config=self.store.configuration(),
                report=WatchReport(job_id=str(job.id), started_at=datetime.now(UTC)),
                record_ids=self.store.pending_ids(),
            )
            self.store.save(checkpoint)
        elapsed_before = checkpoint.elapsed_seconds

        def boundary(*, harvest: bool = False) -> bool:
            checkpoint.elapsed_seconds = elapsed_before + self.clock() - started
            self.store.save(checkpoint)
            context.check_cancellation()
            return checkpoint.elapsed_seconds < (1800 if harvest else 3600)

        try:
            with ResourceFileLock(self.settings.paths.common_dir / "watch-writer.lock"):
                context.check_cancellation()
                context.publish(JobStep.BACKUP)
                if not checkpoint.backup_done:
                    self._backup(checkpoint)
                context.publish(JobStep.HARVEST)
                if not checkpoint.harvest_done and boundary(harvest=True):
                    self.harvester_factory(self.settings, self.store).run(
                        checkpoint, lambda: boundary(harvest=True)
                    )
                context.publish(JobStep.INDEX)
                content = self.content_factory(self.settings, self.store.database)
                for record_id in checkpoint.record_ids:
                    if record_id in checkpoint.completed_record_ids:
                        continue
                    if not boundary():
                        break
                    with closing(self.store.database.connect()) as connection:
                        row = connection.execute(
                            "SELECT relevance_status FROM bibliographic_records WHERE id=?",
                            (record_id,),
                        ).fetchone()
                    if not row or row[0] != "accepted":
                        self.store.finish_item(checkpoint, record_id, None, pending=False)
                        continue
                    acquire = (
                        record_id not in checkpoint.acquisition_record_ids
                        and checkpoint.report.acquisitions_attempted < 100
                    )
                    if acquire:
                        self.store.attempted(record_id)
                        checkpoint.acquisition_record_ids.append(record_id)
                        checkpoint.report.acquisitions_attempted += 1
                        self.store.save(checkpoint)
                    try:
                        ready = content.process(record_id, acquire=acquire)
                        self.store.finish_item(
                            checkpoint,
                            record_id,
                            ready,
                            pending=not ready
                            or (not acquire and record_id not in checkpoint.acquisition_record_ids),
                        )
                        checkpoint.report.errors.extend(getattr(content, "errors", []))
                        if not ready:
                            checkpoint.report.errors.append(f"{record_id}: content_pending")
                    except Exception as error:
                        checkpoint.report.errors.append(f"{record_id}: {type(error).__name__}")
                    self.store.save(checkpoint)
            checkpoint.report.state = "partial" if checkpoint.report.errors else "completed"
        except JobCancelledError:
            checkpoint.report.state = "cancelled"
            raise
        except Exception:
            checkpoint.report.state = "failed"
            raise
        finally:
            pending = self.store.pending_ids()
            checkpoint.report.deferred = len(pending)
            if pending and checkpoint.report.state == "completed":
                checkpoint.report.state = "partial"
            checkpoint.report.completed_at = datetime.now(UTC)
            checkpoint.elapsed_seconds = elapsed_before + self.clock() - started
            self.store.save(checkpoint)
        return JobHandlerResult(
            assistant_content=f"Veille : {checkpoint.report.added_to_rag} ajout(s) au RAG.",
            assistant_response={"bibliographic_watch": checkpoint.report.model_dump(mode="json")},
            response_time_milliseconds=(self.clock() - started) * 1000,
        )
