from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.database.sqlite import Database
from app.ingestion.pipeline import IngestionReport
from app.jobs.background_handlers import CorpusIngestionHandler, LongSynthesisHandler
from app.jobs.contracts import (
    CorpusIngestionPayload,
    JobState,
    JobStep,
    JobType,
    LongSynthesisPayload,
)
from app.jobs.repository import (
    CORPUS_INGESTION_CONVERSATION_ID,
    LONG_SYNTHESIS_CONVERSATION_ID,
    JobRepository,
)
from app.jobs.worker import DurableJobWorker, JobHandlerRegistry
from app.services.workflows import ingest_and_index_paths
from scripts.run_job_worker import build_worker


class FakeExecution:
    result = SimpleNamespace(answer_markdown="# Synthèse\n\nRésultat traçable.")

    def model_dump(self, *, mode: str) -> dict[str, str]:
        return {"mode": mode, "query_id": "query-1"}


def test_long_synthesis_is_idempotently_queued_and_completed(settings) -> None:
    repository = JobRepository(settings.paths.database_path)
    repository.initialize()
    request_id = uuid4()
    payload = LongSynthesisPayload(
        query_id="query-1",
        conversation_id=LONG_SYNTHESIS_CONVERSATION_ID,
        client_request_id=request_id,
    )
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    first = repository.enqueue_long_synthesis(payload, now=now)
    second = repository.enqueue_long_synthesis(payload, now=now)
    calls: list[tuple[str, bool]] = []

    scientific_database = Database(settings.paths.common_database_path)
    scientific_database.initialize()

    def synthesize(_settings, database, *, query_id: str, resume: bool):
        assert database.path == settings.paths.common_database_path
        calls.append((query_id, resume))
        return FakeExecution()

    worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(
            {
                first.type: LongSynthesisHandler(
                    settings,
                    scientific_database,
                    synthesize=synthesize,
                )
            }
        ),
        worker_id="background-synthesis-test",
        lease_duration=timedelta(minutes=5),
        clock=lambda: now,
    )
    completed = worker.run_once()

    assert first.id == second.id
    assert calls == [("query-1", True)]
    assert completed is not None
    assert completed.state is JobState.SUCCEEDED
    assert completed.result_message_id is not None
    with repository.database.connect() as connection:
        steps = [
            row["step"]
            for row in connection.execute(
                "SELECT step FROM job_events WHERE job_id = ? ORDER BY id",
                (str(completed.id),),
            )
        ]
    assert JobStep.SYNTHESIS.value in steps


def test_worker_keeps_long_synthesis_evidence_in_common_corpus(settings) -> None:
    worker = build_worker(settings, job_types=frozenset({JobType.LONG_SYNTHESIS}))
    try:
        handler = worker.registry.resolve(JobType.LONG_SYNTHESIS)
        assert isinstance(handler, LongSynthesisHandler)
        assert handler.database.path == settings.paths.common_database_path
        assert worker.repository.path == settings.paths.database_path
    finally:
        worker.close()


def test_worker_automatically_indexes_corpus_ingestions(settings) -> None:
    worker = build_worker(settings, job_types=frozenset({JobType.CORPUS_INGESTION}))
    try:
        handler = worker.registry.resolve(JobType.CORPUS_INGESTION)
        assert isinstance(handler, CorpusIngestionHandler)
        assert handler.database.path == settings.paths.common_database_path
        assert handler.ingest is ingest_and_index_paths
    finally:
        worker.close()


def test_corpus_ingestion_uses_only_staged_corpus_pdf_and_persists_counts(settings) -> None:
    repository = JobRepository(settings.paths.database_path)
    repository.initialize()
    staged = settings.paths.pdf_dir / "uploads" / "digest.pdf"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"%PDF-1.4")
    payload = CorpusIngestionPayload(
        staged_files=[f"uploads/{staged.name}"],
        conversation_id=CORPUS_INGESTION_CONVERSATION_ID,
        client_request_id=uuid4(),
    )
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    queued = repository.enqueue_corpus_ingestion(payload, now=now)
    received = []

    def ingest(_settings, database, paths, *, progress=None):
        received.extend(paths)
        assert database.path == settings.paths.database_path
        if progress:
            progress(0, 1, paths[0].name, "ingestion")
        return (
            [
                IngestionReport(
                    pdf_path=str(paths[0]),
                    status="chunks_ready",
                    chunk_count=2,
                    duration_seconds=0.01,
                )
            ],
            None,
        )

    worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(
            {
                queued.type: CorpusIngestionHandler(
                    settings,
                    repository.database,
                    ingest=ingest,
                )
            }
        ),
        worker_id="background-ingestion-test",
        lease_duration=timedelta(minutes=5),
        clock=lambda: now,
    )
    completed = worker.run_once()

    assert received == [staged.resolve()]
    assert completed is not None
    assert completed.state is JobState.SUCCEEDED
    conversation = repository.database.chat_conversation(str(completed.conversation_id))
    assert conversation is not None
    response = conversation["messages"][-1]["response"]
    assert response == {
        "document_count": 1,
        "status_counts": {
            "chunks_ready": 1,
            "duplicate": 0,
            "ocr_required": 0,
            "failed": 0,
        },
    }
