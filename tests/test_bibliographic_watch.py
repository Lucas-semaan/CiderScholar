from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.corpora import LOCAL_PROFILE_ENV
from app.database.sqlite import Database
from app.jobs.contracts import JobType
from app.jobs.repository import JobRepository
from app.updates.base import BibliographicApiDeferred
from app.updates.crossref import CrossrefClient
from app.updates.harvest import BibliographicHarvestStore
from app.updates.models import BibliographicRecord
from app.updates.watch_harvest import WatchHarvester, profile_key
from app.updates.watch_models import WatchCheckpoint, WatchConfiguration, WatchReport, WatchTheme
from app.updates.watch_store import WatchStore


@pytest.fixture
def watch(settings, monkeypatch):
    monkeypatch.setenv(LOCAL_PROFILE_ENV, "admin")
    Database(settings.paths.database_path).initialize()
    Database(settings.paths.common_database_path).initialize()
    settings.bibliographic.sources = ["crossref"]
    return WatchStore(settings)


def checkpoint(watch, *, key="microbiologie"):
    config = WatchConfiguration(
        enabled=True, themes=[WatchTheme(key=key, query="cider fermentation")]
    )
    return WatchCheckpoint(
        config=config, report=WatchReport(job_id=str(uuid4()), started_at=datetime.now(UTC))
    )


def record(index=0):
    return BibliographicRecord(
        source="Crossref",
        source_id=f"10.1234/watch-{index}",
        doi=f"10.1234/watch-{index}",
        title=f"Cider fermentation yeast metabolism of apple juice batch {index}",
        abstract=(
            "Cider fermentation with yeast in apple juice: microbiology and organic acid "
            "metabolism."
        ),
        publication_year=2025,
    )


def test_due_catchup_single_job_and_disabling_queued_work(watch):
    now = datetime(2026, 9, 1, tzinfo=UTC)
    assert watch.enqueue(now=now) is None
    watch.configure(WatchConfiguration(enabled=True))
    first = watch.enqueue(now=now)
    assert first.type is JobType.BIBLIOGRAPHIC_WATCH
    assert watch.enqueue(now=now + timedelta(days=6)) is None
    assert watch.enqueue(force=True, now=now).id == first.id
    watch.jobs.cancel_queued(first.id)
    resumed = watch.enqueue(now=now + timedelta(days=30))
    assert resumed.id != first.id
    assert watch.enqueue(now=now + timedelta(days=30)) is None
    watch.configure(WatchConfiguration(enabled=False))
    assert watch.jobs.get(resumed.id).state.value == "cancelled"


def test_watch_claim_waits_for_maintenance_and_excludes_other_workers(watch):
    maintenance = watch.jobs.enqueue_weekly_maintenance()
    watch.enqueue(force=True)
    claimed = watch.jobs.claim_next(worker_id="maintenance", lease_duration=timedelta(minutes=30))
    assert claimed.id == maintenance.id
    assert watch.jobs.claim_next(worker_id="watch", lease_duration=timedelta(minutes=30)) is None
    watch.jobs.request_cancellation(claimed.id)
    watch.jobs.acknowledge_cancellation(claimed.id, worker_id="maintenance")
    claimed = watch.jobs.claim_next(worker_id="watch", lease_duration=timedelta(minutes=30))
    assert claimed.type is JobType.BIBLIOGRAPHIC_WATCH
    watch.jobs.enqueue_weekly_maintenance()
    assert watch.jobs.claim_next(worker_id="another", lease_duration=timedelta(minutes=30)) is None


def test_recent_archive_allocation_and_cursor_resume(watch, settings, monkeypatch):
    calls = []

    def search(_self, _query, limit, *, offset=0, since=None, until=None):
        calls.append((offset, since, until))
        base = offset + (10000 if since else 0)
        return [record(base + index) for index in range(limit)]

    monkeypatch.setattr(CrossrefClient, "search", search)
    cp = checkpoint(watch)
    WatchHarvester(settings, watch).run(cp, lambda: len(calls) < 4)
    assert cp.lane_counts == {"recent": 75, "archive": 25}
    assert cp.report.examined == 100
    assert all(item[1] for item in calls[:3]) and calls[3][1] is None
    assert len(watch.pending_ids()) == 100
    assert watch.checkpoint(cp.report.job_id).report.examined == 100
    next_cp = checkpoint(watch)
    WatchHarvester(settings, watch).run(next_cp, lambda: len(calls) < 5)
    assert calls[-1][0] == 75
    assert calls[-1][1] == calls[0][1]


def test_empty_recent_scan_reallocates_to_archive(watch, settings, monkeypatch):
    calls = []

    def search(_self, _query, limit, *, offset=0, since=None, until=None):
        calls.append(since)
        return [] if since else [record(index + offset) for index in range(limit)]

    monkeypatch.setattr(CrossrefClient, "search", search)
    cp = checkpoint(watch)
    WatchHarvester(settings, watch).run(cp, lambda: len(calls) < 4)
    assert cp.lane_counts == {"recent": 0, "archive": 75}


def test_quota_is_deferred_without_advancing_or_closing_cursor(watch, settings, monkeypatch):
    def search(*_args, **_kwargs):
        raise BibliographicApiDeferred(
            "secret_url_must_not_escape",
            retry_at=datetime.now(UTC) + timedelta(hours=1),
            status_code=429,
        )

    monkeypatch.setattr(CrossrefClient, "search", search)
    cp = checkpoint(watch)
    WatchHarvester(settings, watch).run(cp, lambda: True)
    key = profile_key("crossref", cp.config.themes[0], "recent")
    assert watch.get(key)["offset"] == 0
    assert watch.get(key)["no_gain_rotations"] == 0
    assert cp.report.errors == ["crossref/microbiologie/recent: deferred_429"]
    assert "secret" not in cp.model_dump_json()


def test_page_failure_rolls_back_both_records_and_progress(watch, settings, monkeypatch):
    monkeypatch.setattr(CrossrefClient, "search", lambda *_args, **_kwargs: [record(1), record(2)])
    original = BibliographicHarvestStore.upsert_hit

    def upsert(self, **kwargs):
        if kwargs["rank"] == 2:
            raise RuntimeError("simulated persistence failure")
        return original(self, **kwargs)

    monkeypatch.setattr(BibliographicHarvestStore, "upsert_hit", upsert)
    cp = checkpoint(watch)
    with pytest.raises(RuntimeError, match="persistence failure"):
        WatchHarvester(settings, watch).run(cp, lambda: True)
    assert watch.checkpoint(cp.report.job_id).report.examined == 0
    assert watch.get(profile_key("crossref", cp.config.themes[0], "recent")) is None
    with closing(watch.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM bibliographic_records").fetchone()[0] == 0


def test_custom_theme_admits_relevant_content_and_counts_only_once(watch, settings, monkeypatch):
    monkeypatch.setattr(CrossrefClient, "search", lambda *_args, **_kwargs: [record()])
    cp = checkpoint(watch, key="personnalise")
    WatchHarvester(settings, watch).run(cp, lambda: True)
    assert cp.report.accepted == 1
    assert cp.report.duplicates == 1
    record_id = cp.record_ids[0]
    watch.finish_item(cp, record_id, "abstracts_only", pending=False)
    assert cp.report.added_to_rag == 1
    later = checkpoint(watch)
    watch.finish_item(later, record_id, "full_articles", pending=False)
    assert later.report.added_to_rag == 0


def test_already_indexed_notice_is_not_a_new_addition(watch, settings, monkeypatch):
    bibliography = BibliographicHarvestStore(watch.database)
    run_id, _ = bibliography.start_run(
        settings, themes={"microbiologie": "cider"}, sources=["crossref"]
    )
    record_id = bibliography.upsert_hit(
        run_id=run_id, theme="microbiologie", rank=1, record=record()
    )
    bibliography.update_embedding_status([record_id], "indexed")
    monkeypatch.setattr(CrossrefClient, "search", lambda *_args, **_kwargs: [record()])
    cp = checkpoint(watch)
    WatchHarvester(settings, watch).run(cp, lambda: True)
    watch.finish_item(cp, record_id, "abstracts_only", pending=False)
    assert cp.report.added_to_rag == 0


def test_configuration_api_is_strict_admin_only_and_persistent(settings, monkeypatch):
    from app.main import create_app

    with TestClient(create_app(settings)) as client:
        assert client.get("/api/admin/bibliographic-watch").status_code == 403
        assert client.post("/api/admin/bibliographic-watch/launch").status_code == 403
    monkeypatch.setenv(LOCAL_PROFILE_ENV, "admin")
    with TestClient(create_app(settings)) as client:
        route = "/api/admin/bibliographic-watch"
        assert len(client.get(route).json()["configuration"]["themes"]) == 8
        assert (
            client.put(route + "/configuration", json={"secret": "not-allowed"}).status_code == 422
        )
        result = client.put(route + "/configuration", json={"enabled": True})
        assert result.status_code == 200
        assert result.json()["active_job"]["type"] == "bibliographic_watch"
    assert WatchStore(settings).configuration().enabled is True
    assert len(JobRepository(settings.paths.database_path).list_active_diagnostics()) == 1


def test_handler_indexes_new_content_and_reuses_it_without_recounting(watch, settings, monkeypatch):
    from app.jobs.worker import DurableJobWorker, JobHandlerRegistry
    from app.updates.watch_content import ready_content
    from app.updates.watch_handler import BibliographicWatchHandler

    monkeypatch.setattr(CrossrefClient, "search", lambda *_args, **_kwargs: [record()])
    watch.configure(checkpoint(watch).config)
    calls = []

    class Content:
        def __init__(self, _settings, database):
            self.database = database

        def process(self, record_id, *, acquire):
            calls.append((record_id, acquire))
            BibliographicHarvestStore(self.database).update_embedding_status([record_id], "indexed")
            with closing(self.database.connect()) as connection:
                return ready_content(connection, record_id)

    handler = BibliographicWatchHandler(settings, content_factory=Content)
    worker = DurableJobWorker(
        repository=watch.jobs,
        registry=JobHandlerRegistry({JobType.BIBLIOGRAPHIC_WATCH: handler}),
        worker_id="watch-test",
    )
    first = watch.enqueue(force=True)
    completed = worker.run_once()
    assert completed.state.value == "succeeded"
    report = watch.checkpoint(str(first.id)).report
    assert report.added_to_rag == report.abstracts_only == 1
    assert report.full_articles == 0
    assert report.deferred == 0
    assert len(calls) == 1
    assert list((settings.paths.common_dir / "database" / "watch-backups").glob("*.sqlite3"))
    second = watch.enqueue(force=True)
    assert worker.run_once().state.value == "succeeded"
    assert watch.checkpoint(str(second.id)).report.added_to_rag == 0


def test_index_failure_retains_backlog_and_counts_only_after_recovery(watch, settings, monkeypatch):
    from app.jobs.worker import DurableJobWorker, JobHandlerRegistry
    from app.updates.watch_handler import BibliographicWatchHandler

    monkeypatch.setattr(CrossrefClient, "search", lambda *_args, **_kwargs: [record()])
    watch.configure(checkpoint(watch).config)
    fail = True

    class Content:
        def __init__(self, *_args):
            pass

        def process(self, record_id, *, acquire):
            if fail:
                raise RuntimeError("provider_key_must_never_appear")
            return "abstracts_only"

    worker = DurableJobWorker(
        repository=watch.jobs,
        registry=JobHandlerRegistry(
            {
                JobType.BIBLIOGRAPHIC_WATCH: BibliographicWatchHandler(
                    settings, content_factory=Content
                )
            }
        ),
        worker_id="watch-test",
    )
    first = watch.enqueue(force=True)
    worker.run_once()
    report = watch.checkpoint(str(first.id)).report
    assert report.added_to_rag == 0 and report.deferred == 1
    assert report.state == "partial"
    assert "provider_key" not in report.model_dump_json()
    fail = False
    second = watch.enqueue(force=True)
    worker.run_once()
    assert watch.checkpoint(str(second.id)).report.added_to_rag == 1
    assert watch.pending_ids() == []


def test_full_text_failure_still_indexes_abstract(watch, settings, monkeypatch):
    from app.updates import watch_content

    bibliography = BibliographicHarvestStore(watch.database)
    run_id, _ = bibliography.start_run(
        settings, themes={"microbiologie": "cider"}, sources=["crossref"]
    )
    record_id = bibliography.upsert_hit(
        run_id=run_id, theme="microbiologie", rank=1, record=record()
    )

    def acquisition(*_args, **_kwargs):
        raise TimeoutError("remote_secret")

    def index(_settings, store, _backend, *, record_ids, max_batches):
        assert record_ids == [record_id] and max_batches == 1
        store.update_embedding_status(record_ids, "indexed")

    monkeypatch.setattr(watch_content.FullTextHarvestService, "run", acquisition)
    monkeypatch.setattr(watch_content, "SentenceTransformerBackend", lambda *_: object())
    monkeypatch.setattr(watch_content, "index_bibliographic_abstracts", index)
    content = watch_content.WatchContent(settings, watch.database)
    assert content.process(record_id, acquire=True) == "abstracts_only"
    assert content.errors == [f"{record_id}: acquisition_TimeoutError"]


def test_watch_migration_preserves_existing_jobs_and_events(settings, monkeypatch):
    from app.database import migrations

    current = migrations.CURRENT_SCHEMA_VERSION
    monkeypatch.setattr(migrations, "CURRENT_SCHEMA_VERSION", 35)
    jobs = JobRepository(settings.paths.database_path)
    jobs.initialize()
    job = jobs.enqueue_weekly_maintenance()
    with closing(jobs.database.connect()) as connection:
        before = [tuple(row) for row in connection.execute("SELECT * FROM job_events")]
    monkeypatch.setattr(migrations, "CURRENT_SCHEMA_VERSION", current)
    jobs.initialize()
    assert jobs.get(job.id) == job
    with closing(jobs.database.connect()) as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM job_events")] == before
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    assert jobs.enqueue_bibliographic_watch().type is JobType.BIBLIOGRAPHIC_WATCH
