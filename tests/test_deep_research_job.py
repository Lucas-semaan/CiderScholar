from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from app.corpora import CorpusScope
from app.deep_research.contextual_summary import ContextualSummarizer
from app.deep_research.models import ContextualSummaryResult
from app.deep_research.pipeline import build_deep_research_operations
from app.jobs.contracts import DeepResearchPayload, JobStep, JobType
from app.jobs.deep_research_handler import DeepResearchHandler
from app.jobs.repository import JobRepository
from app.jobs.worker import DurableJobWorker, JobHandlerRegistry
from app.llm.argo_client import ArgoUnavailableError
from app.retrieval.lexical_search import LexicalSearchResult
from app.retrieval.multi_corpus import (
    MultiCorpusLexicalResponse,
    MultiCorpusVectorResponse,
)
from app.retrieval.vector_search import VectorSearchResult
from scripts.run_job_worker import build_worker


class RestartableOperations:
    def __init__(self) -> None:
        self.calls = {
            "search": 0,
            "reranking": 0,
            "evidence": 0,
            "verification": 0,
            "synthesis": 0,
        }
        self.fail_verification_once = True

    def search(self, _payload: DeepResearchPayload) -> None:
        self.calls["search"] += 1

    def extract_evidence(self, _payload: DeepResearchPayload) -> None:
        self.calls["evidence"] += 1

    def confirm_reranking(self, _payload: DeepResearchPayload) -> None:
        self.calls["reranking"] += 1

    def verify(self, _payload: DeepResearchPayload) -> None:
        self.calls["verification"] += 1
        if self.fail_verification_once:
            self.fail_verification_once = False
            raise ArgoUnavailableError("simulated restart boundary")

    def synthesize(self, _payload: DeepResearchPayload) -> str:
        self.calls["synthesis"] += 1
        return "Synthèse approfondie persistée."


class CancellingOperations(RestartableOperations):
    def __init__(self, repository: JobRepository) -> None:
        super().__init__()
        self.repository = repository
        self.job_id = None
        self.fail_verification_once = False

    def search(self, payload: DeepResearchPayload) -> None:
        super().search(payload)
        assert self.job_id is not None
        requested = self.repository.request_cancellation(self.job_id)
        assert requested is not None


def test_deep_research_resumes_checkpoint_without_resubmitting_question(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation = repository.database.create_chat_conversation("Analyse")
    conversation_id = UUID(conversation["id"])
    payload = DeepResearchPayload(
        message="Comparer les preuves en texte intégral.",
        conversation_id=conversation_id,
        client_request_id=uuid4(),
    )
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    first_enqueue = repository.enqueue_deep_research(payload, now=now)
    duplicate_enqueue = repository.enqueue_deep_research(payload, now=now)
    operations = RestartableOperations()
    checkpoint_root = tmp_path / "checkpoints"

    first_worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(
            {JobType.DEEP_RESEARCH: DeepResearchHandler(checkpoint_root, operations)}
        ),
        worker_id="deep-worker-before-restart",
        clock=lambda: now,
    )
    deferred = first_worker.run_once()
    assert deferred is not None
    assert deferred.state.value == "queued"
    assert deferred.step is JobStep.VERIFICATION

    second_worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(
            {JobType.DEEP_RESEARCH: DeepResearchHandler(checkpoint_root, operations)}
        ),
        worker_id="deep-worker-after-restart",
        clock=lambda: deferred.available_at,
    )
    completed = second_worker.run_once()

    assert first_enqueue.job.type is JobType.DEEP_RESEARCH
    assert duplicate_enqueue.job.id == first_enqueue.job.id
    assert duplicate_enqueue.created is False
    assert completed is not None
    assert completed.state.value == "succeeded"
    assert operations.calls == {
        "search": 1,
        "reranking": 1,
        "evidence": 1,
        "verification": 2,
        "synthesis": 1,
    }
    with closing(repository.database.connect()) as connection:
        progress_steps = [
            row["step"]
            for row in connection.execute(
                "SELECT step FROM job_events WHERE job_id = ? ORDER BY id",
                (str(first_enqueue.job.id),),
            )
        ]
    assert all(
        step in progress_steps
        for step in ("search", "reranking", "evidence", "verification", "synthesis")
    )
    assert progress_steps.index("search") < progress_steps.index("reranking")
    assert progress_steps.index("reranking") < progress_steps.index("evidence")
    assert progress_steps.index("evidence") < progress_steps.index("verification")
    assert progress_steps.index("verification") < progress_steps.index("synthesis")
    stored = repository.get(first_enqueue.job.id)
    assert stored is not None
    assert isinstance(stored.payload, DeepResearchPayload)
    persisted_conversation = repository.database.chat_conversation(str(conversation_id))
    assert persisted_conversation is not None
    assert [message["role"] for message in persisted_conversation["messages"]] == [
        "user",
        "assistant",
    ]


def test_deep_research_cancellation_stops_at_the_next_checkpoint(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation = repository.database.create_chat_conversation("Annulation")
    payload = DeepResearchPayload(
        message="Arrêter après la recherche.",
        conversation_id=UUID(conversation["id"]),
        client_request_id=uuid4(),
    )
    enqueued = repository.enqueue_deep_research(payload)
    operations = CancellingOperations(repository)
    operations.job_id = enqueued.job.id
    worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(
            {
                JobType.DEEP_RESEARCH: DeepResearchHandler(
                    tmp_path / "checkpoints",
                    operations,
                )
            }
        ),
        worker_id="deep-worker-cancel",
    )

    completed = worker.run_once()

    assert completed is not None
    assert completed.state.value == "cancelled"
    assert operations.calls == {
        "search": 1,
        "reranking": 0,
        "evidence": 0,
        "verification": 0,
        "synthesis": 0,
    }
    persisted = repository.database.chat_conversation(str(payload.conversation_id))
    assert persisted is not None
    assert [message["role"] for message in persisted["messages"]] == [
        "user",
        "assistant",
    ]
    terminal_notice = persisted["messages"][-1]["response"]
    assert terminal_notice == {
        "kind": "job_terminal_notice",
        "job_id": str(enqueued.job.id),
        "state": "cancelled",
        "error_code": None,
        "diagnostic_code": None,
    }


class ScopedLexicalBackend:
    def __init__(self) -> None:
        self.requested_scopes: list[tuple[CorpusScope, ...]] = []

    def search(self, query, *, limit_per_scope=None, scopes=()):
        assert limit_per_scope == 40
        self.requested_scopes.append(scopes)
        return MultiCorpusLexicalResponse(
            query=query,
            results=[
                LexicalSearchResult(
                    rank=1,
                    chunk_id=1,
                    article_id="common-article",
                    article_title="Commun",
                    publication_year=2026,
                    section="Results",
                    page_start=2,
                    page_end=2,
                    text="common full-text content",
                    bm25_score=-1.0,
                    relevance_score=0.9,
                    scope=CorpusScope.COMMON,
                )
            ],
            duration_seconds_by_scope={scope: 0.01 for scope in scopes},
            duration_seconds=0.02,
        )


class ScopedVectorBackend:
    def __init__(self) -> None:
        self.requested_scopes: list[tuple[CorpusScope, ...]] = []

    def search(self, query, *, limit_per_scope=None, scopes=()):
        assert limit_per_scope == 40
        self.requested_scopes.append(scopes)
        return MultiCorpusVectorResponse(
            query=query,
            results=[
                VectorSearchResult(
                    chunk_id=7,
                    article_id="common-vector-article",
                    score=0.8,
                    section="Discussion",
                    page_start=4,
                    page_end=5,
                    text="common vector full-text content",
                    scope=CorpusScope.COMMON,
                )
            ],
            duration_seconds_by_scope={scope: 0.01 for scope in scopes},
            duration_seconds=0.02,
        )


def test_production_worker_runs_scoped_search_through_durable_stages(settings) -> None:
    repository = JobRepository(settings.paths.database_path)
    repository.initialize()
    conversation = repository.database.create_chat_conversation("Analyse interne")
    payload = DeepResearchPayload(
        message="Comparer les fragments disponibles.",
        conversation_id=UUID(conversation["id"]),
        client_request_id=uuid4(),
    )
    enqueued = repository.enqueue_deep_research(payload)
    lexical = ScopedLexicalBackend()
    vector = ScopedVectorBackend()
    response = MagicMock()
    response.content = json.dumps({"summary": "Résumé contextuel borné.", "relevance_score": 0.8})
    client = MagicMock()
    client.chat.return_value = response
    operations = build_deep_research_operations(
        settings,
        lexical=lexical,
        vector=vector,
        contextual_summarizer=ContextualSummarizer(client),
    )
    worker = build_worker(settings, deep_research_operations=operations)

    completed = worker.run_once()
    search_call_count = len(lexical.requested_scopes)
    cached_conversation = repository.database.create_chat_conversation("Analyse en cache")
    cached_payload = payload.model_copy(
        update={
            "conversation_id": UUID(cached_conversation["id"]),
            "client_request_id": uuid4(),
        }
    )
    cached_enqueued = repository.enqueue_deep_research(cached_payload)
    cached_completed = worker.run_once()
    worker.close()

    assert completed is not None
    assert completed.id == enqueued.job.id
    assert completed.state.value == "succeeded"
    assert cached_completed is not None
    assert cached_completed.id == cached_enqueued.job.id
    assert cached_completed.state.value == "succeeded"
    assert len(lexical.requested_scopes) == search_call_count
    assert len(vector.requested_scopes) == search_call_count
    assert client.chat.call_count == 2
    cached_response = repository.database.chat_conversation(str(cached_payload.conversation_id))[
        "messages"
    ][-1]["response"]
    assert cached_response["details"]["cache"]["hit"] is True
    expected_scopes = (CorpusScope.COMMON,)
    assert lexical.requested_scopes
    assert vector.requested_scopes
    assert set(lexical.requested_scopes) == {expected_scopes}
    assert set(vector.requested_scopes) == {expected_scopes}
    snapshot = operations.retrieval.load(payload)
    assert {(hit.scope, hit.article_id) for hit in snapshot.hits} == {
        (CorpusScope.COMMON, "common-article"),
        (CorpusScope.COMMON, "common-vector-article"),
    }
    assert snapshot.contextual_summary_attempted is True
    assert len(snapshot.contextual_summaries) == 2
    assert all(
        isinstance(summary, ContextualSummaryResult) for summary in snapshot.contextual_summaries
    )
    assert snapshot.contextual_evidence is not None
    assert snapshot.contextual_evidence.source_summary_count == 2
    assert snapshot.contextual_evidence.rejected_summary_count == 0
    assert tuple(snapshot.contextual_evidence.accepted) == operations.retrieval.contextual_evidence(
        payload
    )
    persisted = operations.retrieval._path(payload).read_text(encoding="utf-8")
    assert "full-text content" not in persisted
    response = repository.database.chat_conversation(str(payload.conversation_id))["messages"][-1][
        "response"
    ]
    assert response["details"]["epistemic_claims"] == []
    assert response["details"]["claim_admission"] == []
    assert response["details"]["readiness"]["outcome"] == "abstain"
    assert response["details"]["citations"] == []
    assert response["details"]["cache"]["hit"] is False
