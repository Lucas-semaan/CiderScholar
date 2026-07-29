from __future__ import annotations

from datetime import UTC, datetime

from app.jobs.contracts import JobState, JobType, WeeklyMaintenancePayload
from app.jobs.repository import JobRepository


def test_weekly_maintenance_reuses_durable_queue_and_persistent_singleton(settings) -> None:
    repository = JobRepository(settings.paths.database_path)
    repository.initialize()
    requested_at = datetime(2026, 7, 22, 9, tzinfo=UTC)

    first = repository.enqueue_weekly_maintenance(now=requested_at)
    same_from_second_process = JobRepository(
        settings.paths.database_path
    ).enqueue_weekly_maintenance(now=requested_at)

    assert first.id == same_from_second_process.id
    assert first.type is JobType.WEEKLY_MAINTENANCE
    assert first.state is JobState.QUEUED
    assert isinstance(first.payload, WeeklyMaintenancePayload)
    assert first.payload.requested_at == requested_at


def test_new_maintenance_can_be_enqueued_after_terminal_cycle(settings) -> None:
    repository = JobRepository(settings.paths.database_path)
    repository.initialize()
    first = repository.enqueue_weekly_maintenance()
    with repository.database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET state = 'failed' WHERE id = ?",
            (str(first.id),),
        )

    second = repository.enqueue_weekly_maintenance()

    assert second.id != first.id
