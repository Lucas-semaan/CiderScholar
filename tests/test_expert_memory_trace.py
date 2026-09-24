from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.database.migrations import CURRENT_SCHEMA_VERSION
from app.database.sqlite import Database
from app.jobs.chat_handler import _shadow_routing_trace, _trace_manifest
from app.knowledge.models import KnowledgePackage
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.trace import (
    ExpertRunManifest,
    TraceBudget,
    TraceCost,
    TraceEvidence,
    TraceIdentity,
    TraceOutput,
    TraceRoutingDecision,
)
from app.models.chatbot import (
    ChatbotCitationAnchor,
    ChatbotCitationEvidence,
    ChatbotResult,
    ChatbotTraceCandidate,
)


def _manifest() -> ExpertRunManifest:
    digest = "a" * 64
    now = datetime(2026, 9, 23, 12, tzinfo=UTC)
    return ExpertRunManifest(
        run_id=uuid4(),
        job_id=uuid4(),
        attempt=0,
        code_revision="test-revision",
        question_sha256=digest,
        user_context_sha256=digest,
        answer_effort="balanced",
        interaction_mode="research",
        configuration_sha256=digest,
        sql_schema_version=47,
        routing_items=(TraceIdentity(item_id="method.test", revision=1, content_sha256=digest),),
        routing_decisions=(TraceRoutingDecision(route_id="route.test", reason="no_match"),),
        budgets=(TraceBudget(stage="generation", limit_characters=1600, used_characters=120),),
        evidence=(
            TraceEvidence(
                evidence_id="chunk:article-1:2",
                source_kind="chunk",
                source_id="article-1:2",
                text_sha256=digest,
                presented_text_sha256=digest,
                page_start=2,
                page_end=2,
            ),
        ),
        output=TraceOutput(state="succeeded", response_sha256=digest),
        cost=TraceCost(llm_requests=1, prompt_tokens=10, completion_tokens=20),
        created_at=now,
        updated_at=now,
    )


def test_manifest_is_closed_and_hashed_canonically() -> None:
    manifest = _manifest()

    assert len(manifest.manifest_sha256()) == 64
    assert manifest.manifest_sha256() == manifest.model_copy().manifest_sha256()


def test_manifest_rejects_duplicate_claim_evidence_ids() -> None:
    manifest = _manifest()
    payload = manifest.model_dump(mode="python")
    payload["output"] = {
        "state": "succeeded",
        "claim_links": [{"claim_id": "claim-1", "evidence_ids": ("e1", "e1")}],
    }

    try:
        ExpertRunManifest.model_validate(payload)
    except ValueError as error:
        assert "unique" in str(error)
    else:
        raise AssertionError("duplicate evidence IDs must be rejected")


def test_manifest_rejects_claims_that_escape_persisted_evidence() -> None:
    payload = _manifest().model_dump(mode="python")
    payload["output"] = {
        "state": "succeeded",
        "claim_links": [{"claim_id": "claim-1", "evidence_ids": ("missing",)}],
    }

    try:
        ExpertRunManifest.model_validate(payload)
    except ValueError as error:
        assert "unknown evidence" in str(error)
    else:
        raise AssertionError("claim links must reference persisted evidence")


def test_current_schema_creates_trace_correction_diagnosis_candidate_evaluation_and_review_tables(
    settings,
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()

    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "expert_run_manifests" in names
        assert "expert_corrections" in names
        assert "expert_correction_revisions" in names
        assert "expert_diagnoses" in names
        assert "expert_candidates" in names
        assert "expert_evaluations" in names
        assert "expert_reviews" in names
        assert "expert_activation_events" in names
        assert (
            connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            == CURRENT_SCHEMA_VERSION
        )


def test_chat_trace_manifest_records_presented_context_candidates(settings) -> None:
    digest = "b" * 64
    evidence = ChatbotCitationEvidence(
        evidence_id="common:article-1:abstract",
        snippet="Persisted abstract passage.",
        source_text_sha256=digest,
        presented_text_sha256=digest,
    )
    result = ChatbotResult(
        message="Question",
        retrieval_query="Question",
        answer_markdown="Réponse [1].",
        sources=[],
        citation_anchors=[
            ChatbotCitationAnchor(
                citation_id="cite-0123456789abcdef",
                display_index=1,
                label="[1]",
                record_id="common:article-1",
                source_family="scientific_publication",
                title="Article",
                evidence=[evidence],
            )
        ],
        retrieval_trace_candidates=[
            ChatbotTraceCandidate(
                item_id="common:article-1",
                content_sha256=digest,
                stage="semantic_filter",
                rank=0,
                decision="rejected",
                reason="global_semantic_grade_c_or_d",
            )
        ],
        trace_corpus_fingerprint_before="c" * 64,
        trace_corpus_fingerprint_after="d" * 64,
        warnings=[],
        model="test-model",
        local_result_count=1,
        external_result_count=0,
        external_enrichment_used=False,
        prompt_tokens=10,
        completion_tokens=5,
        duration_seconds=0.1,
    )
    assert "retrieval_trace_candidates" not in result.model_dump(mode="json")
    payload = SimpleNamespace(
        message="Question",
        answer_effort="balanced",
        expert_memory_pin=SimpleNamespace(
            mode="off",
            release_id=None,
            release_sha256=None,
            recipe_version=None,
            recipe_sha256=None,
        ),
    )
    job = SimpleNamespace(id=uuid4(), attempt=0, payload=payload)

    manifest = _trace_manifest(
        job=job,
        settings=settings,
        history=[],
        interaction_mode="research",
        state="succeeded",
        result=result,
        now=datetime(2026, 9, 23, 12, tzinfo=UTC),
    )

    assert len(manifest.candidates) == 2
    assert {candidate.stage for candidate in manifest.candidates} == {
        "semantic_filter",
        "final_context",
    }
    rejected = next(
        candidate for candidate in manifest.candidates if candidate.stage == "semantic_filter"
    )
    assert rejected.decision == "rejected"
    assert rejected.identity.content_sha256 == digest
    assert manifest.corpus_fingerprint_before == "c" * 64
    assert manifest.corpus_fingerprint_after == "d" * 64


def test_shadow_routing_is_traced_without_injecting_instructions(
    settings,
    expert_package_payload,
    monkeypatch,
) -> None:
    package = KnowledgePackage.model_validate(expert_package_payload)
    release_id = uuid4()
    monkeypatch.setattr(
        KnowledgeRepository,
        "load_release",
        lambda _repository, _release_id: (SimpleNamespace(id=release_id), package),
    )
    job = SimpleNamespace(
        id=uuid4(),
        payload=SimpleNamespace(
            message="Question synthétique",
            expert_memory_pin=SimpleNamespace(mode="shadow", release_id=release_id),
        ),
    )

    items, budgets, decisions = _shadow_routing_trace(
        job=job,
        settings=settings,
        database=Database(settings.paths.database_path),
    )

    assert items == ()
    assert [budget.stage for budget in budgets] == [
        "planning",
        "semantic_filter",
        "generation",
    ]
    assert all(budget.used_characters == 0 for budget in budgets)
    assert decisions == ()
