from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.admin.corpus_backup import MaintenanceBackup
from app.admin.maintenance_handler import WeeklyMaintenanceHandler
from app.admin.maintenance_operations import MaintenanceOperationResult, MaintenancePublication
from app.admin.maintenance_state import read_last_success
from app.jobs.contracts import JobState, JobStep, JobType
from app.jobs.repository import JobRepository
from app.jobs.worker import DurableJobWorker, JobHandlerRegistry


class FakeMaintenanceOperations:
    def __init__(self, _settings, _maintenance_id: UUID, *, fail_index: bool = False) -> None:
        self.calls: list[str] = []
        self.fail_index = fail_index

    def backup(self) -> MaintenanceBackup:
        self.calls.append("backup")
        return MaintenanceBackup(
            corpus_version=f"corpus-v1-{'a' * 64}",
            version_directory="backup",
            protected_directory="protected",
            archive_sha256="b" * 64,
        )

    def suggestions(self) -> MaintenanceOperationResult:
        self.calls.append("suggestions")
        return MaintenanceOperationResult(counters={"suggestions_imported": 2})

    def harvest(self) -> MaintenanceOperationResult:
        self.calls.append("harvest")
        return MaintenanceOperationResult(counters={"harvest_accepted": 3})

    def index(self) -> MaintenanceOperationResult:
        self.calls.append("index")
        if self.fail_index:
            self.fail_index = False
            raise RuntimeError("interrupted index")
        return MaintenanceOperationResult(counters={"abstracts_indexed": 3})

    def validate(self) -> MaintenanceOperationResult:
        self.calls.append("validate")
        return MaintenanceOperationResult(counters={"corpus_vectors": 5})

    def publish(self) -> MaintenancePublication:
        self.calls.append("publish")
        return MaintenancePublication(
            corpus_version=f"corpus-v1-{'c' * 64}",
            version_directory="published",
            latest_path="latest.json",
            archive_sha256="d" * 64,
        )

    def rollback(self, _backup: MaintenanceBackup) -> None:
        self.calls.append("rollback")


def _worker(settings, operations: FakeMaintenanceOperations):
    repository = JobRepository(settings.paths.database_path)
    repository.initialize()
    job = repository.enqueue_weekly_maintenance(now=datetime(2026, 7, 22, tzinfo=UTC))
    handler = WeeklyMaintenanceHandler(
        settings,
        operations_factory=lambda *_: operations,
    )
    worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.WEEKLY_MAINTENANCE: handler}),
        worker_id="maintenance-test",
        lease_duration=timedelta(minutes=5),
        clock=lambda: datetime(2026, 7, 22, 10, tzinfo=UTC),
    )
    return repository, job, worker


def test_full_simulated_maintenance_publishes_then_marks_success(settings) -> None:
    operations = FakeMaintenanceOperations(settings, UUID(int=1))
    repository, job, worker = _worker(settings, operations)

    completed = worker.run_once()

    assert completed is not None
    assert completed.id == job.id
    assert completed.state is JobState.SUCCEEDED
    assert operations.calls == [
        "backup",
        "suggestions",
        "harvest",
        "index",
        "validate",
        "publish",
    ]
    success = read_last_success(settings)
    assert success is not None
    assert success.corpus_version == f"corpus-v1-{'c' * 64}"
    report = (
        settings.paths.data_dir
        / "admin"
        / "maintenance"
        / str(job.payload.maintenance_id)
        / "report.json"
    )
    assert report.is_file()
    assert "document" not in report.read_text(encoding="utf-8").casefold()
    assert repository.get(job.id).step is JobStep.PERSISTENCE


def test_interrupted_mutation_rolls_back_and_restarts_after_backup(settings) -> None:
    operations = FakeMaintenanceOperations(settings, UUID(int=1), fail_index=True)
    repository, job, worker = _worker(settings, operations)

    with pytest.raises(RuntimeError, match="interrupted index"):
        worker.run_once()

    assert operations.calls[-1] == "rollback"
    assert read_last_success(settings) is None
    with repository.database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET state='queued', worker_id=NULL, lease_expires_at=NULL WHERE id=?",
            (str(job.id),),
        )

    completed = worker.run_once()

    assert completed is not None
    assert completed.state is JobState.SUCCEEDED
    assert operations.calls.count("backup") == 1
    assert operations.calls.count("suggestions") == 2
    assert operations.calls.count("publish") == 1
