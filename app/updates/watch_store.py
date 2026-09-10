"""SQLite authority for watch configuration, cursors, and resumable reports."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.database.sqlite import Database
from app.jobs.contracts import JobType
from app.jobs.repository import JobRepository
from app.resource_lock import ResourceFileLock
from app.updates.watch_models import WatchCheckpoint, WatchConfiguration, WatchStatus


class WatchStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.paths.common_database_path)
        self.jobs = JobRepository(settings.paths.database_path)

    def _state_database(self, key: str) -> Database:
        return self.jobs.database if key in {"configuration", "next_due_at"} else self.database

    def get(self, key: str, default: Any = None) -> Any:
        with closing(self._state_database(key).connect()) as connection:
            row = connection.execute(
                "SELECT value_json FROM bibliographic_watch_state WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key: str, value: Any, *, connection: Any = None) -> None:
        if connection is None:
            with self._state_database(key).transaction() as transaction:
                self.put(key, value, connection=transaction)
            return
        connection.execute(
            "INSERT INTO bibliographic_watch_state VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def configuration(self) -> WatchConfiguration:
        return WatchConfiguration.model_validate(self.get("configuration", {}))

    def configure(self, config: WatchConfiguration) -> None:
        with ResourceFileLock(self.settings.paths.common_dir / "watch-scheduler.lock"):
            previous = self.configuration()
            with self.jobs.database.transaction() as connection:
                self.put("configuration", config.model_dump(mode="json"), connection=connection)
                if config.enabled and not previous.enabled:
                    self.put("next_due_at", None, connection=connection)
            if not config.enabled:
                for job in self.jobs.list_active_diagnostics():
                    if job.type is JobType.BIBLIOGRAPHIC_WATCH:
                        self.jobs.cancel_queued(job.id)
                        self.jobs.request_cancellation(job.id)

    def pending_ids(self) -> list[str]:
        with closing(self.database.connect()) as connection:
            return [
                str(row[0])
                for row in connection.execute(
                    "SELECT record_id FROM bibliographic_watch_items WHERE pending=1 ORDER "
                    "BY rowid LIMIT 1000"
                )
            ]

    def attempted(self, record_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE bibliographic_watch_items SET attempted_at=? WHERE record_id=?",
                (datetime.now(UTC).isoformat(), record_id),
            )

    def finish_item(
        self, checkpoint: WatchCheckpoint, record_id: str, ready: str | None, *, pending: bool
    ) -> None:
        candidate = checkpoint.model_copy(deep=True)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT initially_ready, added_by_job FROM bibliographic_watch_items "
                "WHERE record_id=?",
                (record_id,),
            ).fetchone()
            if ready and row and not row[0] and not row[1]:
                candidate.report.added_to_rag += 1
                setattr(candidate.report, ready, getattr(candidate.report, ready) + 1)
                connection.execute(
                    "UPDATE bibliographic_watch_items SET added_by_job=? WHERE record_id=?",
                    (candidate.report.job_id, record_id),
                )
            connection.execute(
                "UPDATE bibliographic_watch_items SET pending=? WHERE record_id=?",
                (int(pending), record_id),
            )
            candidate.completed_record_ids.append(record_id)
            self.save(candidate, connection=connection)
        checkpoint.report = candidate.report
        checkpoint.completed_record_ids = candidate.completed_record_ids

    def checkpoint(self, job_id: str) -> WatchCheckpoint | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                "SELECT checkpoint_json FROM bibliographic_watch_runs WHERE job_id=?", (job_id,)
            ).fetchone()
        return WatchCheckpoint.model_validate_json(row[0]) if row else None

    def save(self, checkpoint: WatchCheckpoint, *, connection: Any = None) -> None:
        if connection is None:
            with self.database.transaction() as transaction:
                self.save(checkpoint, connection=transaction)
            return
        connection.execute(
            "INSERT INTO bibliographic_watch_runs VALUES (?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET checkpoint_json=excluded.checkpoint_json, "
            "updated_at=excluded.updated_at",
            (checkpoint.report.job_id, checkpoint.model_dump_json(), datetime.now(UTC).isoformat()),
        )

    def status(self) -> WatchStatus:
        active = next(
            (
                job
                for job in self.jobs.list_active_diagnostics()
                if job.type is JobType.BIBLIOGRAPHIC_WATCH
            ),
            None,
        )
        active_record = self.jobs.get(active.id) if active else None
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                "SELECT checkpoint_json FROM bibliographic_watch_runs ORDER BY "
                "updated_at DESC LIMIT 20"
            ).fetchall()
        reports = [WatchCheckpoint.model_validate_json(row[0]).report for row in rows]
        for report in reports:
            job = self.jobs.get(report.job_id)
            if job and job.state.value in {"failed", "cancelled"}:
                report.state = job.state.value
        return WatchStatus(
            configuration=self.configuration(),
            next_due_at=self.get("next_due_at"),
            active_job=active_record.to_public() if active_record else None,
            history=reports,
            suspended_reason="Mode hors ligne actif." if self.settings.app.offline_mode else None,
        )

    def enqueue(self, *, force: bool = False, now: datetime | None = None):
        now = now or datetime.now(UTC)
        if self.settings.app.offline_mode:
            return None
        with ResourceFileLock(self.settings.paths.common_dir / "watch-scheduler.lock"):
            config = self.configuration()
            if not config.enabled and not force:
                return None
            due = self.get("next_due_at")
            if not force and due and datetime.fromisoformat(due) > now:
                return None
            job = self.jobs.enqueue_bibliographic_watch(now=now)
            self.put("next_due_at", (now + timedelta(days=7)).isoformat())
            return job
