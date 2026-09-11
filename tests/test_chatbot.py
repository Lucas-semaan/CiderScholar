from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.chat_effort import AnswerEffort, answer_effort_budget
from app.database.sqlite import Database
from app.llm.argo_client import (
    ArgoQuotaError,
    ArgoScientificValidationError,
    ScientificValidationReason,
)
from app.models.chatbot import (
    ChatbotRetrievalTrace,
    ChatEvidencePassage,
    ChatEvidenceRecord,
    ScientificGenerationTrace,
)
from app.retrieval.coverage_assessment import (
    AxisCoverageAssessment,
    CoverageAssessmentResult,
    incomplete_coverage_axis_keys,
)
from app.retrieval.global_semantic_filter import (
    GlobalSemanticDecision,
    GlobalSemanticFilterResult,
)
from app.retrieval.query_planning import (
    QueryPlanningProtocolDiagnostic,
    QueryPlanningProtocolError,
    deterministic_query_plan,
)
from app.retrieval.semantic_filter import (
    AxisSemanticAssessment,
    CandidateSemanticDecision,
    SemanticFilterResult,
)
from app.services.chatbot import (
    chatbot_candidates_from_sources,
    chatbot_sources,
    chatbot_sources_from_evidence,
    contextualize_retrieval_query,
    conversation_context,
    latest_chatbot_sources,
    merge_chatbot_candidates,
    resolve_chat_interaction_mode,
)
from app.services.workflows import (
    _abstract_route_warning,
    _ChatRetrievalResources,
    _ChatRetrievalTraceCollector,
    _fallback_chatbot_result,
    _full_text_intermediate_pool_sizes,
    _initial_retrieval_candidate_limit,
    _query_planning_diagnostic_code,
    acquire_common_full_text_for_chat,
    answer_chatbot,
    search_common_corpus_abstracts,
    search_common_corpus_full_text_evidence,
)
from app.updates.models import BibliographicRecord
from app.updates.vector_index import BibliographicHybridResponse, BibliographicHybridResult


def _local(index: int) -> BibliographicHybridResult:
    return BibliographicHybridResult(
        rank=index,
        record_id=f"local-{index}",
        title=f"Cider fermentation study {index}",
        abstract="Apple cider fermentation and polyphenol evidence.",
        authors=["Ada Test"],
        journal="Cider Science",
        publication_year=2025,
        doi=f"10.1000/local-{index}",
        url=f"https://doi.org/10.1000/local-{index}",
        sources=["OpenAlex"],
        lexical_rank=index,
        vector_rank=index,
        score=0.1,
    )


def _external(source_id: str, doi: str) -> BibliographicRecord:
    return BibliographicRecord(
        source="Crossref",
        source_id=source_id,
        title=f"Apple cider polyphenols {source_id}",
        abstract="Apple cider polyphenols influence bitterness and astringency.",
        authors=["Jean Test"],
        journal="Fermentation",
        publication_year=2026,
        doi=doi,
        url=f"https://doi.org/{doi}",
    )


def test_balanced_first_wave_reduces_cold_candidates_without_reducing_final_limits(
    settings,
) -> None:
    budget = answer_effort_budget(AnswerEffort.BALANCED)

    assert _initial_retrieval_candidate_limit(settings, budget) == 45
    candidate_articles, axis_candidates = _full_text_intermediate_pool_sizes(budget.article_count)
    assert (candidate_articles, axis_candidates) == (24, 12)
    assert candidate_articles >= budget.article_count * 3
    assert budget.abstract_result_limit == 15
    assert budget.evidence_record_limit == 16


def test_abstract_degradation_warning_reports_the_distinct_full_text_route() -> None:
    evidence = [
        ChatEvidenceRecord(
            record_id="common:full-text",
            origin="local_rag",
            evidence_level="full_text",
            scope="common",
            article_id="full-text",
            title="Étude intégrale",
            passages=[
                ChatEvidencePassage(
                    evidence_id="common:full-text:1",
                    chunk_id=1,
                    page_start=1,
                    page_end=1,
                    text="Passage un.",
                ),
                ChatEvidencePassage(
                    evidence_id="common:full-text:2",
                    chunk_id=2,
                    page_start=2,
                    page_end=2,
                    text="Passage deux.",
                ),
            ],
        )
    ]

    warning = _abstract_route_warning(
        "Que montre la fermentation ?",
        diagnostics=["abstract_metadata_query_degraded"],
        abstract_result_count=3,
        full_text_records=evidence,
    )

    assert warning is not None
    assert "voie des résumés bibliographiques" in warning
    assert "recherche distincte dans les textes intégraux" in warning
    assert "1 article(s)" in warning
    assert "2 passage(s)" in warning


def test_validation_fallback_distinguishes_retrieved_documents_from_citations() -> None:
    response = _fallback_chatbot_result(
        message="Que montre la fermentation ?",
        retrieval_query="fermentation",
        evidence=[],
        warnings=[],
        diagnostic_code="unsupported_numeric_claim",
        diagnostic_codes=["unsupported_numeric_claim"],
        started=0.0,
        retrieval_traces=[
            ChatbotRetrievalTrace(
                stage="llm_context",
                selected_article_count=3,
                selected_passage_count=9,
                selected_full_text_article_count=2,
                selected_full_text_passage_count=8,
                selected_abstract_article_count=1,
                selected_abstract_passage_count=1,
            )
        ],
    )

    assert response.sources == []
    assert "2 article(s) en texte intégral" in response.answer_markdown
    assert "8 passage(s)" in response.answer_markdown
    assert "1 notice(s) sur abstract" in response.answer_markdown
    assert "retrouvés, mais ne sont pas présentés comme références citées" in (
        response.answer_markdown
    )


def test_answer_chatbot_rejects_out_of_scope_before_any_rag_request(settings, monkeypatch) -> None:
    def fail_if_called(*_args, **_kwargs):
        pytest.fail("an out-of-scope question must not reach the RAG")

    # Construct the chat database before replacing the workflow dependency: the
    # guard itself must not construct the common-corpus SQLite reader.
    chat_database = Database(settings.paths.database_path)
    monkeypatch.setattr("app.services.workflows.Database", fail_if_called)
    monkeypatch.setattr("app.services.workflows.ArgoClient", fail_if_called)
    monkeypatch.setattr("app.services.workflows.search_common_corpus_abstracts", fail_if_called)
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_full_text_evidence", fail_if_called
    )
    monkeypatch.setattr("app.services.workflows.discover_bibliographic_records", fail_if_called)

    result = answer_chatbot(
        settings,
        chat_database,
        message="Quelle est la capitale de la France ?",
        history=[],
        use_external_sources=True,
    )

    assert result.generation_status == "abstained"
    assert result.diagnostic_code == "out_of_scope"
    assert result.model == "deterministic-scope-guard"
    assert result.sources == []
    assert result.local_result_count == 0
    assert result.external_result_count == 0
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0
    assert result.retrieval_traces == []


def test_covered_axis_without_semantic_ab_evidence_still_requires_follow_up() -> None:
    axis = deterministic_query_plan("Stabilité protéique du jus de pomme").plan.axes[0]
    candidate_id = "common:test-candidate"
    semantic = SemanticFilterResult(
        question="Stabilité protéique du jus de pomme",
        axes=[
            AxisSemanticAssessment(
                axis_key=axis.key,
                decisions=[
                    CandidateSemanticDecision(
                        candidate_id=candidate_id,
                        relevance="irrelevant",
                        rationale="Candidat non admissible pour cet axe.",
                    )
                ],
            )
        ],
        selected_candidate_ids=[],
        model="semantic-test",
        prompt_tokens=0,
        completion_tokens=0,
    )
    coverage = CoverageAssessmentResult(
        question="Stabilité protéique du jus de pomme",
        axes=[
            AxisCoverageAssessment(
                axis_key=axis.key,
                status="covered",
                supporting_candidate_ids=[candidate_id],
                assessment="Couverture déclarée sans preuve A/B propre à l'axe.",
            )
        ],
        model="coverage-test",
        prompt_tokens=0,
        completion_tokens=0,
    )

    assert incomplete_coverage_axis_keys(
        [axis],
        coverage,
        semantic,
        minimum_candidates_per_axis=1,
    ) == {axis.key}


def test_query_planning_warning_exposes_only_a_stable_non_sensitive_code() -> None:
    error = QueryPlanningProtocolError(
        QueryPlanningProtocolDiagnostic(
            category="schema_validation",
            pydantic_path=("axes", 0, "search_queries"),
            pydantic_type="missing",
        )
    )

    assert _query_planning_diagnostic_code(error) == (
        "argo_protocol.schema_validation.axes.0.search_queries.missing"
    )


def test_deep_initial_wave_is_exact_bounded_and_keeps_more_than_twenty_candidates(
    settings,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeArgoClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    class FakePlanningService:
        def __init__(self, _client, **_options):
            pass

        def plan(self, question, **_options):
            return deterministic_query_plan(question, deep=True)

    def fake_abstract_search(*_args, **options):
        captured["abstract"] = options
        return [_local(index) for index in range(1, 33)]

    def fake_full_text_search(*_args, **options):
        captured["full_text"] = options
        return []

    def fake_merge(local, external, *, limit):
        captured["merge_local_count"] = len(local)
        captured["merge_external_count"] = len(external)
        captured["merge_limit"] = limit
        return [], 0

    monkeypatch.setattr("app.services.workflows.ArgoClient", FakeArgoClient)
    monkeypatch.setattr("app.services.workflows.ArgoQueryPlanningService", FakePlanningService)
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_abstracts",
        fake_abstract_search,
    )
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_full_text_evidence",
        fake_full_text_search,
    )
    monkeypatch.setattr("app.services.workflows.merge_chatbot_candidates", fake_merge)

    result = answer_chatbot(
        settings,
        Database(settings.paths.database_path),
        message="État de l'art approfondi des facteurs de fermentation du cidre.",
        history=[],
        use_external_sources=False,
        answer_effort=AnswerEffort.DEEP,
    )

    abstract_options = captured["abstract"]
    full_text_options = captured["full_text"]
    assert abstract_options["limit"] == 32
    assert abstract_options["prefix_matching"] is False
    assert abstract_options["candidate_limit"] == 96
    assert full_text_options["article_count"] == 16
    assert full_text_options["prefix_matching"] is False
    assert full_text_options["include_fallback_variants"] is False
    assert captured["merge_local_count"] == 32
    assert captured["merge_external_count"] == 0
    assert captured["merge_limit"] == 32
    lock_timing = next(timing for timing in result.timings if timing.stage == "retrieval_lock_wait")
    assert lock_timing.count == 1


def test_chatbot_context_uses_recent_user_intent_only() -> None:
    history = [
        {"role": "user", "content": "Parle-moi des polyphénols"},
        {"role": "assistant", "content": "Réponse avec sources"},
        {"role": "user", "content": "Et pendant le pressurage ?"},
    ]

    query = contextualize_retrieval_query("Quelles conséquences ?", history)
    context = conversation_context(history)

    assert query == ("Parle-moi des polyphénols Et pendant le pressurage ? Quelles conséquences ?")
    assert context == history


def test_chatbot_auto_mode_reuses_sources_for_details_and_format_changes() -> None:
    history = [
        {"role": "user", "content": "Quels facteurs influencent la fermentation ?"},
        {"role": "assistant", "content": "Réponse scientifique avec sources."},
    ]

    assert (
        resolve_chat_interaction_mode(
            "Peux-tu détailler ce point ?",
            history,
            "auto",
            has_reusable_sources=True,
        )
        == "conversation"
    )
    assert (
        resolve_chat_interaction_mode(
            "Reformule la réponse sous forme de tableau.",
            history,
            "auto",
            has_reusable_sources=True,
        )
        == "conversation"
    )


def test_chatbot_auto_mode_searches_for_explicitly_new_literature() -> None:
    history = [{"role": "assistant", "content": "Réponse scientifique avec sources."}]

    assert (
        resolve_chat_interaction_mode(
            "Cherche de nouvelles publications sur les levures non-Saccharomyces.",
            history,
            "auto",
            has_reusable_sources=True,
        )
        == "research"
    )
    assert (
        resolve_chat_interaction_mode(
            "Présente cela plus brièvement.",
            history,
            "research",
            has_reusable_sources=True,
        )
        == "research"
    )


def test_chatbot_rebuilds_context_from_latest_persisted_sources() -> None:
    source = chatbot_sources([_local(1)], ["local-1"])[0]
    messages = [
        {"role": "assistant", "content": "Ancienne réponse", "response": None},
        {
            "role": "assistant",
            "content": "Réponse récente",
            "response": {"sources": [source.model_dump(mode="json")]},
        },
    ]

    persisted_sources = latest_chatbot_sources(messages)
    candidates = chatbot_candidates_from_sources(persisted_sources)

    assert [candidate.record_id for candidate in candidates] == ["local-1"]
    assert candidates[0].abstract == source.snippet
    assert candidates[0].authors == ["Ada Test"]


def test_chatbot_merges_qualified_external_sources_without_doi_duplicates() -> None:
    local = [_local(index) for index in range(1, 9)]
    external = [
        _external("duplicate", "10.1000/local-1"),
        _external("fresh-1", "10.1000/external-1"),
        _external("fresh-2", "10.1000/external-2"),
        _external("fresh-3", "10.1000/external-3"),
        _external("fresh-4", "10.1000/external-4"),
        BibliographicRecord(
            source="Crossref",
            source_id="noise",
            title="Antiacne properties of cashew apple",
            abstract="A topical dermatology study.",
        ),
    ]

    merged, external_count = merge_chatbot_candidates(local, external)

    assert len(merged) == 10
    assert external_count == 4
    assert [record.record_id for record in merged[:6]] == [
        "local-1",
        "local-2",
        "local-3",
        "local-4",
        "local-5",
        "local-6",
    ]
    assert all(record.record_id.startswith("external:") for record in merged[6:])
    assert len({record.doi for record in merged}) == 10


def test_chatbot_merge_accepts_the_deep_answer_candidate_budget() -> None:
    local = [_local(index) for index in range(1, 33)]

    merged, external_count = merge_chatbot_candidates(local, [], limit=32)

    assert len(merged) == 32
    assert external_count == 0
    assert [record.rank for record in merged] == list(range(1, 33))


def test_chatbot_returns_only_cited_source_cards() -> None:
    local = [_local(1)]
    merged, _ = merge_chatbot_candidates(local, [_external("fresh", "10.1000/external")])

    sources = chatbot_sources(merged, [merged[-1].record_id])

    assert len(sources) == 1
    assert sources[0].origin == "external_api"
    assert sources[0].evidence_level == "abstract"
    assert sources[0].doi == "10.1000/external"


def test_chatbot_full_text_source_persists_chunks_and_pages_for_follow_up(
    settings,
) -> None:
    database = Database(settings.paths.common_database_path)
    database.initialize()
    settings.paths.common_pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = settings.paths.common_pdf_dir / "article-1.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nsource\n")
    database.save_article_and_chunks(
        {
            "id": "article-1",
            "sha256": "9" * 64,
            "title": "Cider fermentation temperature",
            "authors": ["Ada Test"],
            "pdf_path": str(pdf_path),
        },
        [
            {
                "page_start": 7,
                "page_end": 8,
                "chunk_index": 0,
                "text": "The full article reports the temperature-dependent kinetics.",
                "token_count": 7,
            }
        ],
    )
    evidence = ChatEvidenceRecord(
        record_id="common:article-1",
        origin="local_rag",
        evidence_level="full_text",
        scope="common",
        article_id="article-1",
        title="Cider fermentation temperature",
        authors=["Ada Test"],
        providers=["local"],
        passages=[
            ChatEvidencePassage(
                evidence_id="common:article-1:chunk:12",
                chunk_id=12,
                section="Results",
                page_start=7,
                page_end=8,
                text="The full article reports the temperature-dependent kinetics.",
            ),
            ChatEvidencePassage(
                evidence_id="common:article-1:chunk:13",
                chunk_id=13,
                section="Discussion",
                page_start=9,
                page_end=9,
                text="The discussion compares the observed kinetics.",
            ),
        ],
    )

    sources = chatbot_sources_from_evidence(
        [evidence],
        ["common:article-1:chunk:12"],
        database,
    )

    assert len(sources) == 1
    assert sources[0].evidence_level == "full_text"
    assert sources[0].article_id == "article-1"
    assert sources[0].local_pdf_url == "/api/corpus/article-1/pdf"
    assert sources[0].chunk_ids == [12]
    assert sources[0].page_ranges == ["7-8"]
    assert sources[0].snippet.startswith("The full article")


def test_answer_chatbot_prefers_full_text_over_the_matching_abstract(
    settings,
    monkeypatch,
) -> None:
    passage_id = "common:article-1:chunk:12"
    full_text = ChatEvidenceRecord(
        record_id="common:article-1",
        origin="local_rag",
        evidence_level="full_text",
        scope="common",
        article_id="article-1",
        title="Cider fermentation study 1",
        authors=["Ada Test"],
        doi="10.1000/local-1",
        journal="Cider Science",
        publication_year=2025,
        providers=["local"],
        passages=[
            ChatEvidencePassage(
                evidence_id=passage_id,
                chunk_id=12,
                section="Results",
                page_start=7,
                page_end=8,
                text="The full article reports temperature-dependent fermentation kinetics.",
            )
        ],
    )
    captured: dict[str, object] = {}

    class FakeArgoClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    class FakeEvidenceService:
        def __init__(self, _client, **_options):
            pass

        def answer(self, _question, records, **_kwargs):
            captured["records"] = records
            captured["reasoning_wiki"] = self.organizational_reasoning_context
            return SimpleNamespace(
                answer_markdown="Réponse fondée sur le texte intégral.",
                cited_evidence_ids=[passage_id],
                source_record_ids=["common:article-1"],
                model="test-model",
                prompt_tokens=10,
                completion_tokens=5,
            )

    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_abstracts",
        lambda *_args, **_kwargs: [_local(1)],
    )
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_full_text_evidence",
        lambda *_args, **_kwargs: [full_text],
    )
    monkeypatch.setattr("app.services.workflows.ArgoClient", FakeArgoClient)
    monkeypatch.setattr(
        "app.services.workflows.CiderEvidenceRagService",
        FakeEvidenceService,
    )

    result = answer_chatbot(
        settings,
        Database(settings.paths.database_path),
        message="Quels facteurs influencent la fermentation ?",
        history=[],
        use_external_sources=False,
    )

    assert [record.evidence_level for record in captured["records"]] == ["full_text"]
    assert "# Le cœur de raisonnement AsCoCid" in captured["reasoning_wiki"]
    assert "# Piloter la fermentation et la cuverie" in captured["reasoning_wiki"]
    assert result.answer_markdown == "Réponse fondée sur le texte intégral."
    assert result.sources[0].evidence_level == "full_text"
    assert result.sources[0].page_ranges == ["7-8"]


def test_answer_chatbot_applies_the_multilingual_semantic_selection_before_synthesis(
    settings,
    monkeypatch,
) -> None:
    relevant = _local(1).model_copy(
        update={
            "title": "Protein haze formation in apple juice",
            "abstract": (
                "Les protéines du jus de pomme s'agrègent avec les polyphénols et forment "
                "un trouble colloïdal."
            ),
        }
    )
    generation_trace = ScientificGenerationTrace(
        phase="evidence",
        outcome="generated",
        request_count=1,
        validation_retries=0,
        length_retries=0,
        prompt_tokens=2,
        completion_tokens=1,
    )
    noise = _local(2).model_copy(
        update={
            "title": "Patulin quantification by chromatography",
            "abstract": "Patulin was quantified in apple products by HPLC.",
        }
    )
    captured: dict[str, object] = {}

    class FakeArgoClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    class FakeGlobalSemanticFilter:
        def __init__(self, _client, **_options):
            pass

        def filter_records(self, question, _needs, evidence, **_options):
            assert "stabilité protéique" in question
            decisions = [
                GlobalSemanticDecision(
                    candidate_id=record.record_id,
                    relevance=(
                        "direct" if record.record_id == relevant.record_id else "irrelevant"
                    ),
                    supported_need_ids=["v1"] if record.record_id == relevant.record_id else [],
                    rationale=(
                        "Correspondance mécanistique multilingue."
                        if record.record_id == relevant.record_id
                        else "Le dosage de patuline ne traite pas la stabilité protéique."
                    ),
                )
                for record in evidence
            ]
            return GlobalSemanticFilterResult(
                question=question,
                decisions=decisions,
                selected_candidate_ids=[relevant.record_id],
                model="semantic-test",
                prompt_tokens=7,
                completion_tokens=5,
                used_fallback=True,
                warnings=["Internal fallback detail that must not reach the reader."],
            )

    class FakeEvidenceService:
        def __init__(self, _client, *, correction_temperature):
            captured["correction_temperature"] = correction_temperature

        @staticmethod
        def _result(records, **options):
            captured["records"] = records
            captured["coverage_notes"] = options["coverage_notes"]
            return SimpleNamespace(
                answer_markdown="La stabilité dépend des interactions protéines-polyphénols.",
                cited_evidence_ids=[f"{relevant.record_id}:abstract"],
                source_record_ids=[relevant.record_id],
                model="answer-test",
                prompt_tokens=2,
                completion_tokens=1,
                facet_drafts=[],
                generation_traces=[generation_trace],
            )

        def answer(self, _question, records, **options):
            return self._result(records, **options)

        def answer_faceted(self, _question, records, **options):
            return self._result(records, **options)

    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_abstracts",
        lambda *_args, **_kwargs: [relevant, noise],
    )
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_full_text_evidence",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.workflows.ArgoGlobalSemanticEvidenceFilter",
        FakeGlobalSemanticFilter,
    )
    monkeypatch.setattr("app.services.workflows.ArgoClient", FakeArgoClient)
    monkeypatch.setattr(
        "app.services.workflows.CiderEvidenceRagService",
        FakeEvidenceService,
    )

    progress_stages = []
    result = answer_chatbot(
        settings,
        Database(settings.paths.database_path),
        message="Fais un état de l'art sur la stabilité protéique des jus de pomme.",
        history=[],
        use_external_sources=False,
        on_progress=progress_stages.append,
    )

    assert [record.record_id for record in captured["records"]] == [relevant.record_id]
    assert captured["correction_temperature"] == 0.1
    assert captured["coverage_notes"] == ()
    assert result.prompt_tokens == 9
    assert result.completion_tokens == 6
    assert result.generation_traces == [generation_trace]
    assert not any("validation sémantique globale" in warning for warning in result.warnings)
    assert not any("Internal fallback detail" in warning for warning in result.warnings)
    traces = {trace.stage: trace for trace in result.retrieval_traces}
    assert traces["evidence_merge"].pre_rerank_candidate_count == 2
    assert traces["semantic_filter"].rejection_counts == {"global_semantic_grade_c_or_d": 1}
    assert traces["llm_context"].selected_article_count == 1
    assert traces["llm_context"].selected_passage_count == 1
    trace_payload = str([trace.model_dump() for trace in result.retrieval_traces])
    assert relevant.record_id not in trace_payload
    assert relevant.title not in trace_payload
    generation_timing = next(
        timing for timing in result.timings if timing.stage == "argo_generation"
    )
    assert generation_timing.prompt_tokens == 2
    assert generation_timing.completion_tokens == 1
    assert generation_timing.process_rss_before_gb == pytest.approx(0.25)
    assert generation_timing.process_rss_after_gb == pytest.approx(0.25)
    assert [source.record_id for source in result.sources] == [relevant.record_id]
    assert progress_stages == [
        "planning",
        "search",
        "reranking",
        "evidence_selection",
        "generation",
    ]


def test_answer_chatbot_runs_one_grouped_wave_without_axis_follow_up(
    settings,
    monkeypatch,
) -> None:
    message = "Fais un état de l'art sur la stabilité protéique des jus de pomme."
    planned = deterministic_query_plan(message)
    base_axis = planned.plan.axes[0]
    covered_axis = base_axis.model_copy(
        update={
            "key": "protein_presence",
            "label": "Présence des protéines",
            "question": "Quelles protéines sont présentes dans le jus de pomme ?",
            "search_queries": [
                "apple juice haze active proteins",
                "apple juice protein composition",
            ],
        }
    )
    incomplete_axis = base_axis.model_copy(
        update={
            "key": "aggregation_mechanism",
            "label": "Mécanismes d'agrégation",
            "question": "Quels mécanismes provoquent l'agrégation colloïdale ?",
            "search_queries": [
                "apple juice colloidal aggregation",
                "protein polyphenol haze mechanism",
            ],
        }
    )
    planned = planned.model_copy(
        update={
            "plan": planned.plan.model_copy(
                update={
                    "axes": [covered_axis, incomplete_axis],
                    "retrieval_queries": [
                        *covered_axis.search_queries,
                        *incomplete_axis.search_queries,
                    ],
                    "requires_faceted_answer": True,
                }
            )
        }
    )
    initial = _local(1).model_copy(
        update={
            "title": "Apple juice haze-active proteins",
            "abstract": "Haze-active proteins were detected in apple juice.",
        }
    )
    supplemental = _local(2).model_copy(
        update={
            "title": "Protein polyphenol aggregation mechanisms",
            "abstract": (
                "Protein-polyphenol aggregation and heat treatment governed colloidal haze."
            ),
        }
    )
    calls = {"abstract": 0, "full_text": 0, "assessment": 0}
    captured: dict[str, object] = {}
    qdrant_owners: dict[str, list[object]] = {"abstract": [], "full_text": []}

    class FakeArgoClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    class FakePlanningService:
        def __init__(self, _client, **_options):
            pass

        def plan(self, _question, **_options):
            return planned

    def fake_abstract_search(*_args, **options):
        calls["abstract"] += 1
        qdrant_owners["abstract"].append(options["qdrant_client_owner"])
        captured["grouped_queries"] = options["search_queries"]
        return [initial]

    def fake_full_text_search(*_args, **options):
        calls["full_text"] += 1
        qdrant_owners["full_text"].append(options["qdrant_client_owner"])
        captured["axis_queries"] = options["axis_queries"]
        return []

    def fake_filter_and_coverage(
        _settings,
        *,
        question,
        axes,
        evidence,
        on_argo_reserved,
        on_coverage_started,
    ):
        del on_argo_reserved
        on_coverage_started()
        calls["assessment"] += 1
        decisions = [
            CandidateSemanticDecision(
                candidate_id=record.record_id,
                relevance="direct",
                rationale="Preuve scientifiquement liée à l'axe.",
            )
            for record in evidence
        ]
        selected_ids = [record.record_id for record in evidence]
        semantic = SemanticFilterResult(
            question=question,
            axes=[AxisSemanticAssessment(axis_key=axis.key, decisions=decisions) for axis in axes],
            selected_candidate_ids=selected_ids,
            model="semantic-test",
            prompt_tokens=5,
            completion_tokens=3,
        )
        is_first_pass = calls["assessment"] == 1
        coverage = CoverageAssessmentResult(
            question=question,
            axes=[
                AxisCoverageAssessment(
                    axis_key=axis.key,
                    status=(
                        "partial"
                        if is_first_pass and axis.key == incomplete_axis.key
                        else "covered"
                    ),
                    supporting_candidate_ids=[
                        (
                            supplemental.record_id
                            if not is_first_pass and axis.key == incomplete_axis.key
                            else initial.record_id
                        )
                    ],
                    assessment=(
                        "Le mécanisme manque."
                        if is_first_pass and axis.key == incomplete_axis.key
                        else "Le mécanisme est maintenant couvert."
                    ),
                    missing_information=(
                        ["Mécanismes d'agrégation protéines-polyphénols"]
                        if is_first_pass and axis.key == incomplete_axis.key
                        else []
                    ),
                    suggested_queries=(
                        ["apple juice protein polyphenol aggregation haze"]
                        if is_first_pass and axis.key == incomplete_axis.key
                        else []
                    ),
                )
                for axis in axes
            ],
            model="coverage-test",
            prompt_tokens=4,
            completion_tokens=2,
        )
        return semantic, coverage

    class FakeEvidenceService:
        def __init__(self, _client, **_options):
            pass

        @staticmethod
        def _result(records, **options):
            captured["records"] = records
            captured["coverage_notes"] = options["coverage_notes"]
            return SimpleNamespace(
                answer_markdown="Synthèse complétée.",
                cited_evidence_ids=[f"{initial.record_id}:abstract"],
                source_record_ids=[initial.record_id],
                model="answer-test",
                prompt_tokens=2,
                completion_tokens=1,
                facet_drafts=[],
            )

        def answer(self, _question, records, **options):
            return self._result(records, **options)

        def answer_faceted(self, _question, records, **options):
            return self._result(records, **options)

    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_abstracts",
        fake_abstract_search,
    )
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_full_text_evidence",
        fake_full_text_search,
    )
    monkeypatch.setattr(
        "app.services.workflows._semantic_filter_and_coverage",
        fake_filter_and_coverage,
        raising=False,
    )
    monkeypatch.setattr("app.services.workflows.ArgoClient", FakeArgoClient)
    monkeypatch.setattr("app.services.workflows.ArgoQueryPlanningService", FakePlanningService)
    monkeypatch.setattr(
        "app.services.workflows.CiderEvidenceRagService",
        FakeEvidenceService,
    )

    result = answer_chatbot(
        settings,
        Database(settings.paths.database_path),
        message=message,
        history=[],
        use_external_sources=False,
    )

    assert calls == {"abstract": 1, "full_text": 1, "assessment": 0}
    assert captured["axis_queries"] is None
    assert "apple juice haze active proteins" in captured["grouped_queries"]
    assert "apple juice colloidal aggregation" in captured["grouped_queries"]
    assert qdrant_owners["abstract"][0] is qdrant_owners["full_text"][0]
    assert {record.record_id for record in captured["records"]} == {initial.record_id}
    assert captured["coverage_notes"] == ()
    assert result.prompt_tokens == 2
    assert result.completion_tokens == 1


def test_answer_chatbot_never_downgrades_planning_when_argo_quota_is_reached(
    settings,
    monkeypatch,
) -> None:
    class FakeArgoClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    class QuotaPlanningService:
        def __init__(self, _client, **_options):
            pass

        def plan(self, *_args, **_kwargs):
            raise ArgoQuotaError("quota reached")

    monkeypatch.setattr("app.services.workflows.ArgoClient", FakeArgoClient)
    monkeypatch.setattr(
        "app.services.workflows.ArgoQueryPlanningService",
        QuotaPlanningService,
    )
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_abstracts",
        lambda *_args, **_kwargs: pytest.fail("retrieval must wait for complete ARGO planning"),
    )

    with pytest.raises(ArgoQuotaError):
        answer_chatbot(
            settings,
            Database(settings.paths.database_path),
            message="Quels facteurs influencent la fermentation ?",
            history=[],
            use_external_sources=False,
        )


def test_answer_chatbot_does_not_restart_retrieval_after_late_quota(
    settings,
    monkeypatch,
) -> None:
    candidate = _local(1)
    calls = {"abstract": 0, "generation": 0}

    class FakeArgoClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    class QuotaEvidenceService:
        def __init__(self, _client, **_options):
            pass

        def answer(self, *_args, **_kwargs):
            calls["generation"] += 1
            raise ArgoQuotaError("quota reached after retrieval")

        answer_faceted = answer

    def abstract_search(*_args, **_kwargs):
        calls["abstract"] += 1
        return [candidate]

    monkeypatch.setattr("app.services.workflows.ArgoClient", FakeArgoClient)
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_abstracts",
        abstract_search,
    )
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_full_text_evidence",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.workflows._semantic_filter_and_coverage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ArgoQuotaError("late quota")),
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.workflows.CiderEvidenceRagService",
        QuotaEvidenceService,
    )

    result = answer_chatbot(
        settings,
        Database(settings.paths.database_path),
        message="Quels facteurs influencent la fermentation ?",
        history=[],
        use_external_sources=False,
    )

    assert calls == {"abstract": 1, "generation": 1}
    assert result.generation_status == "diagnostic_only"
    assert result.diagnostic_code == "provider_quota_after_retrieval"


def test_answer_chatbot_returns_structured_diagnostic_when_argo_synthesis_is_invalid(
    settings,
    monkeypatch,
) -> None:
    candidate = _local(1)

    class FakeArgoClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    class InvalidEvidenceService:
        def __init__(self, _client, **_options):
            pass

        def answer(self, *_args, **_kwargs):
            trace = ScientificGenerationTrace(
                phase="evidence",
                outcome="failed",
                request_count=2,
                validation_retries=1,
                length_retries=0,
                correction_temperature=0.1,
                prompt_tokens=33,
                completion_tokens=11,
                validation_codes=[
                    "unsupported_numeric_claim",
                    "missing_required_evidence",
                ],
                presented_evidence_count=2,
                cited_evidence_count=1,
            )
            raise ArgoScientificValidationError(
                "unsupported numeric claim; omitted one or more relevant evidence elements",
                reasons=[
                    ScientificValidationReason.UNSUPPORTED_NUMERIC_CLAIM,
                    ScientificValidationReason.MISSING_REQUIRED_EVIDENCE,
                ],
                prompt_tokens=33,
                completion_tokens=11,
                generation_traces=[trace],
            )

        def answer_faceted(self, *_args, **_kwargs):
            return self.answer()

    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_abstracts",
        lambda *_args, **_kwargs: [candidate],
    )
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_full_text_evidence",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr("app.services.workflows.ArgoClient", FakeArgoClient)
    monkeypatch.setattr(
        "app.services.workflows.CiderEvidenceRagService",
        InvalidEvidenceService,
    )

    result = answer_chatbot(
        settings,
        Database(settings.paths.database_path),
        message="Quels facteurs influencent la fermentation ?",
        history=[],
        use_external_sources=False,
    )

    assert result.generation_status == "validation_failed"
    assert result.diagnostic_code == "unsupported_numeric_claim"
    assert result.diagnostic_codes == [
        "unsupported_numeric_claim",
        "missing_required_evidence",
    ]
    assert result.prompt_tokens == 33
    assert result.completion_tokens == 11
    assert result.generation_traces[0].outcome == "failed"
    assert result.generation_traces[0].presented_evidence_count == 2
    assert result.generation_traces[0].cited_evidence_count == 1
    assert result.model == "deterministic-structured-fallback"
    assert result.sources == []
    assert "## Réponse synthétique" in result.answer_markdown
    assert "passages les mieux classés" not in result.answer_markdown


def test_invalid_faceted_schema_has_a_stable_non_unknown_diagnostic() -> None:
    error = ArgoScientificValidationError(
        "ARGO returned an invalid faceted evidence answer: schema mismatch"
    )

    assert error.reason is ScientificValidationReason.INVALID_SCHEMA


def test_answer_chatbot_returns_a_diagnostic_when_retrieval_is_empty(
    settings,
    monkeypatch,
) -> None:
    class FakeArgoClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_abstracts",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.workflows.search_harvested_abstracts",
        lambda *_args, **_kwargs: SimpleNamespace(results=[]),
    )
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_full_text_evidence",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr("app.services.workflows.ArgoClient", FakeArgoClient)

    result = answer_chatbot(
        settings,
        Database(settings.paths.database_path),
        message="Question cidricole dont le retrieval est simulé vide",
        history=[],
        use_external_sources=False,
    )

    assert result.generation_status == "diagnostic_only"
    assert result.diagnostic_code == "retrieval_no_qualified_evidence"
    assert result.sources == []
    assert "## Réponse synthétique" in result.answer_markdown
    assert "## Limites des preuves" in result.answer_markdown


def test_answer_chatbot_returns_a_diagnostic_when_local_retrieval_crashes(
    settings,
    monkeypatch,
) -> None:
    class FakeArgoClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    def fail_retrieval(*_args, **_kwargs):
        raise RuntimeError("simulated local retrieval failure")

    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_abstracts",
        fail_retrieval,
    )
    monkeypatch.setattr(
        "app.services.workflows.search_harvested_abstracts",
        fail_retrieval,
    )
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_full_text_evidence",
        fail_retrieval,
    )
    monkeypatch.setattr("app.services.workflows.ArgoClient", FakeArgoClient)

    result = answer_chatbot(
        settings,
        Database(settings.paths.database_path),
        message="Question cidricole avec panne de retrieval simulée",
        history=[],
        use_external_sources=False,
    )

    assert result.generation_status == "diagnostic_only"
    assert result.diagnostic_code == "retrieval_unavailable"
    assert any("RuntimeError" in warning for warning in result.warnings)


def test_answer_chatbot_abstains_without_exposing_candidates_when_semantic_filter_selects_nothing(
    settings,
    monkeypatch,
) -> None:
    candidate = _local(1)
    evidence = ChatEvidenceRecord(
        record_id=candidate.record_id,
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title=candidate.title,
        authors=candidate.authors,
        doi=candidate.doi,
        journal=candidate.journal,
        publication_year=candidate.publication_year,
        providers=candidate.sources,
        evidence_grade="D",
        passages=[
            ChatEvidencePassage(
                evidence_id=f"{candidate.record_id}:abstract",
                text=candidate.abstract,
            )
        ],
    )

    class FakeArgoClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    class RejectAllGlobalSemanticFilter:
        def __init__(self, _client, **_options):
            pass

        def filter_records(self, question, _needs, _evidence, **_options):
            return GlobalSemanticFilterResult(
                question=question,
                decisions=[
                    GlobalSemanticDecision(
                        candidate_id=candidate.record_id,
                        relevance="irrelevant",
                        rationale="Rejet simulé pour tester le repli.",
                    )
                ],
                selected_candidate_ids=[],
                model="semantic-test",
                prompt_tokens=1,
                completion_tokens=1,
            )

    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_abstracts",
        lambda *_args, **_kwargs: [candidate],
    )
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_full_text_evidence",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.workflows.merge_chat_evidence",
        lambda *_args, **_kwargs: [evidence],
    )
    monkeypatch.setattr(
        "app.services.workflows.ArgoGlobalSemanticEvidenceFilter",
        RejectAllGlobalSemanticFilter,
    )
    monkeypatch.setattr("app.services.workflows.ArgoClient", FakeArgoClient)

    result = answer_chatbot(
        settings,
        Database(settings.paths.database_path),
        message="Question cidricole avec rejet sémantique simulé",
        history=[],
        use_external_sources=False,
    )

    assert result.generation_status == "abstained"
    assert result.diagnostic_code == "semantic_filter_empty"
    assert result.sources == []
    assert candidate.abstract not in result.answer_markdown
    assert "## Limites des preuves" in result.answer_markdown


def test_answer_chatbot_uses_one_validated_synthesis_for_multidimensional_research(
    settings,
    monkeypatch,
) -> None:
    candidate = _local(1).model_copy(
        update={
            "title": "Apple brandy aged with toasted oak chips",
            "abstract": (
                "Oak aging changed volatile esters, phenolic compounds, acidity, "
                "color and sensory properties of apple brandy."
            ),
            "doi": "10.1000/apple-brandy",
        }
    )
    evidence_id = f"{candidate.record_id}:abstract"
    captured: dict[str, object] = {}

    class FakeArgoClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    class FakeEvidenceService:
        def __init__(self, _client, **_options):
            pass

        def answer(self, _question, records, **kwargs):
            captured["records"] = records
            captured["coverage_notes"] = kwargs["coverage_notes"]
            return SimpleNamespace(
                answer_markdown="Réponse finale validée.",
                cited_evidence_ids=[evidence_id],
                source_record_ids=[candidate.record_id],
                model="test-model",
                prompt_tokens=30,
                completion_tokens=20,
                facet_drafts=[],
            )

        def answer_faceted(self, *_args, **_kwargs):
            raise AssertionError("the production pipeline no longer generates facet drafts")

    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_abstracts",
        lambda *_args, **_kwargs: [candidate],
    )

    def fake_full_text_retrieval(*_args, **options):
        captured["axis_queries"] = options["axis_queries"]
        return []

    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_full_text_evidence",
        fake_full_text_retrieval,
    )

    def accept_all(_settings, question, axes, evidence, on_coverage_started, **_kwargs):
        on_coverage_started()
        semantic = SemanticFilterResult(
            question=question,
            axes=[
                AxisSemanticAssessment(
                    axis_key=axis.key,
                    decisions=[
                        CandidateSemanticDecision(
                            candidate_id=evidence[0].record_id,
                            relevance="direct",
                            rationale="Direct test evidence.",
                        )
                    ],
                )
                for axis in axes
            ],
            selected_candidate_ids=[evidence[0].record_id],
            model="semantic-test",
            prompt_tokens=1,
            completion_tokens=1,
        )
        coverage = CoverageAssessmentResult(
            question=question,
            axes=[
                AxisCoverageAssessment(
                    axis_key=axis.key,
                    status="covered",
                    supporting_candidate_ids=[evidence[0].record_id],
                    assessment="Covered by direct test evidence.",
                )
                for axis in axes
            ],
            model="coverage-test",
            prompt_tokens=1,
            completion_tokens=1,
        )
        return semantic, coverage

    monkeypatch.setattr(
        "app.services.workflows._semantic_filter_and_coverage", accept_all, raising=False
    )
    monkeypatch.setattr("app.services.workflows.ArgoClient", FakeArgoClient)
    monkeypatch.setattr(
        "app.services.workflows.CiderEvidenceRagService",
        FakeEvidenceService,
    )

    result = answer_chatbot(
        settings,
        Database(settings.paths.database_path),
        message="Impact de l'élevage en barrique sur les arômes et la structure du Calvados ?",
        history=[],
        use_external_sources=False,
    )

    assert captured["axis_queries"] is None
    assert captured["coverage_notes"] == ()
    assert captured["records"][0].doi == "10.1000/apple-brandy"
    assert result.answer_markdown == "Réponse finale validée."
    assert result.facet_drafts == []


def test_chat_acquisition_targets_abstract_notices_and_indexes_common_corpus(
    settings,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeHarvestService:
        def __init__(self, scoped_settings, database):
            captured["database_path"] = database.path
            captured["pdf_dir"] = scoped_settings.paths.pdf_dir

        def run(self, **kwargs):
            captured["run"] = kwargs
            return (
                object(),
                SimpleNamespace(
                    article_ids=["global-article"],
                    errors=[],
                ),
            )

    def fake_index(scoped_settings, database, **kwargs):
        captured["indexed_database_path"] = database.path
        captured["indexed_article_ids"] = kwargs["article_ids"]
        captured["qdrant_client_owner"] = kwargs["qdrant_client_owner"]

    monkeypatch.setattr(
        "app.services.workflows.FullTextHarvestService",
        FakeHarvestService,
    )
    monkeypatch.setattr("app.services.workflows.index_pending_chunks", fake_index)
    candidate = _local(1).model_copy(update={"record_id": "common-abstract:record-1"})
    qdrant_client_owner = object()

    article_ids, warnings = acquire_common_full_text_for_chat(
        settings,
        [candidate],
        qdrant_client_owner=qdrant_client_owner,
    )

    assert article_ids == ["global-article"]
    assert warnings == []
    assert captured["database_path"] == settings.paths.common_database_path
    assert captured["indexed_database_path"] == settings.paths.common_database_path
    assert captured["pdf_dir"] == settings.paths.common_pdf_dir
    assert captured["indexed_article_ids"] == ["global-article"]
    assert captured["qdrant_client_owner"] is qdrant_client_owner
    assert captured["run"] == {
        "include_slow_fallbacks": False,
        "max_downloads": 2,
        "record_ids": ["record-1"],
    }


def test_chatbot_never_uses_more_than_four_live_api_sources() -> None:
    external = [_external(f"fresh-{index}", f"10.1000/external-{index}") for index in range(5)]

    merged, external_count = merge_chatbot_candidates([], external)

    assert len(merged) == 4
    assert external_count == 4


def test_chatbot_uses_the_default_common_corpus_for_an_exact_article_title(settings) -> None:
    database = Database(settings.paths.common_database_path)
    database.initialize()
    title = "Potential Evaluation and Modeling of Biogas Production from Apple Pomace"
    database.save_article_and_chunks(
        {
            "id": "common-biogas",
            "sha256": "b" * 64,
            "doi": "10.1000/biogas",
            "title": title,
            "abstract": "Apple pomace was evaluated as a substrate for biogas production.",
            "authors": ["Ada Test"],
            "journal": "Waste Science",
            "publication_year": 2025,
            "language": "en",
            "pdf_path": "data/common/pdf/biogas.pdf",
            "validation_status": "validated",
            "source": "corpus-base",
        },
        [
            {
                "section": "Title",
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": f"{title}. Apple pomace biogas production was modeled.",
                "token_count": 12,
            }
        ],
    )

    trace = _ChatRetrievalTraceCollector()
    results = search_common_corpus_abstracts(
        settings,
        query=title,
        limit=5,
        retrieval_trace=trace,
    )
    sources = chatbot_sources(results, [results[0].record_id])

    assert [result.record_id for result in results] == ["common:common-biogas"]
    assert results[0].title == title
    assert sources[0].scope.value == "common"
    assert sources[0].providers == ["corpus-base"]
    abstract_trace = trace.models()[0]
    assert abstract_trace.stage == "abstract_search"
    assert abstract_trace.query_variant_count == 1
    assert abstract_trace.lexical_candidate_count >= 1
    assert abstract_trace.selected_article_count == 1
    assert title not in str(abstract_trace.model_dump())


def test_grouped_abstract_failure_keeps_per_query_results_and_typed_diagnostic(
    settings,
    monkeypatch,
) -> None:
    Database(settings.paths.common_database_path).initialize()
    calls = {"grouped": 0, "single": 0, "closed": 0}

    class FakeHarvestStore:
        def __init__(self, _database):
            pass

        def statistics(self):
            return {"abstracts": 1}

    class FakeHybridService:
        def __init__(self, *_args):
            self.index = SimpleNamespace(close=lambda: None)

        def search_many(self, *_args, **_kwargs):
            calls["grouped"] += 1
            raise ValueError("simulated grouped SQLite incompatibility")

        def search(self, query, **_kwargs):
            calls["single"] += 1
            result = _local(calls["single"]).model_copy(
                update={
                    "record_id": f"harvest-{calls['single']}",
                    "abstract": f"Result retained for {query}.",
                    "doi": f"10.1000/grouped-{calls['single']}",
                }
            )
            return BibliographicHybridResponse(
                query=query,
                results=[result],
                lexical_candidate_count=1,
                duration_seconds=0.01,
            )

        def close(self):
            calls["closed"] += 1

    monkeypatch.setattr("app.services.workflows.BibliographicHarvestStore", FakeHarvestStore)
    monkeypatch.setattr(
        "app.services.workflows.SentenceTransformerBackend",
        lambda _settings: object(),
    )
    monkeypatch.setattr(
        "app.services.workflows.BibliographicVectorIndex",
        lambda _settings, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "app.services.workflows.BibliographicHybridSearchService",
        FakeHybridService,
    )

    diagnostics: list[str] = []
    trace = _ChatRetrievalTraceCollector()
    results = search_common_corpus_abstracts(
        settings,
        query="cider fermentation",
        search_queries=["malolactic fermentation"],
        limit=5,
        diagnostics=diagnostics,
        retrieval_trace=trace,
    )

    assert calls == {"grouped": 1, "single": 2, "closed": 1}
    assert len(results) == 2
    assert all(result.record_id.startswith("common-abstract:") for result in results)
    assert diagnostics == ["abstract_grouped_hybrid_invalid_data"]
    assert trace.models()[0].rejection_counts["abstract_grouped_hybrid_invalid_data"] == 1


def test_chatbot_retrieves_page_bound_full_text_even_without_an_abstract(settings) -> None:
    database = Database(settings.paths.common_database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "full-text-only",
            "sha256": "c" * 64,
            "doi": "10.1000/full-text-only",
            "title": "Detailed cider fermentation kinetics",
            "abstract": None,
            "authors": ["Ada Test"],
            "journal": "Cider Science",
            "publication_year": 2025,
            "language": "en",
            "pdf_path": "data/common/pdf/full-text-only.pdf",
            "validation_status": "validated",
            "source": "corpus-base",
        },
        [
            {
                "section": "Results",
                "page_start": 6,
                "page_end": 7,
                "chunk_index": 0,
                "text": (
                    "Fermentation kinetics were controlled by yeast assimilable nitrogen "
                    "during the cider trial."
                ),
                "token_count": 12,
            }
        ],
    )

    trace = _ChatRetrievalTraceCollector()
    records = search_common_corpus_full_text_evidence(
        settings,
        query="yeast assimilable nitrogen fermentation kinetics",
        article_count=3,
        retrieval_trace=trace,
    )

    assert [record.article_id for record in records] == ["full-text-only"]
    assert records[0].evidence_level == "full_text"
    assert records[0].passages[0].page_start == 6
    assert records[0].passages[0].page_end == 7
    assert records[0].passages[0].chunk_id is not None
    traces = {item.stage: item for item in trace.models()}
    assert traces["full_text_search"].query_variant_count >= 1
    assert traces["full_text_search"].lexical_candidate_count >= 1
    assert traces["full_text_search"].rrf_unique_candidate_count >= 1
    assert traces["full_text_reranking"].pre_rerank_candidate_count == 1
    assert traces["full_text_evidence_selection"].selected_article_count == 1
    assert traces["full_text_evidence_selection"].selected_passage_count >= 1
    assert traces["full_text_evidence_selection"].selected_full_text_article_count == 1
    assert traces["full_text_evidence_selection"].selected_full_text_passage_count >= 1
    assert traces["full_text_evidence_selection"].selected_abstract_article_count == 0


def test_full_text_retrieval_cache_reuses_only_a_fully_validated_result(settings) -> None:
    database = Database(settings.paths.common_database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "cached-full-text",
            "sha256": "d" * 64,
            "doi": "10.1000/cached-full-text",
            "title": "Cached cider fermentation kinetics",
            "abstract": "Yeast nitrogen controls cider fermentation kinetics.",
            "authors": ["Ada Test"],
            "journal": "Cider Science",
            "publication_year": 2025,
            "language": "en",
            "pdf_path": "data/common/pdf/cached-full-text.pdf",
            "validation_status": "validated",
            "source": "corpus-base",
        },
        [
            {
                "section": "Results",
                "page_start": 3,
                "page_end": 3,
                "chunk_index": 0,
                "text": "Yeast assimilable nitrogen controlled cider fermentation kinetics.",
                "token_count": 9,
            }
        ],
    )
    resources = _ChatRetrievalResources()
    first_trace = _ChatRetrievalTraceCollector()
    second_trace = _ChatRetrievalTraceCollector()
    try:
        first = search_common_corpus_full_text_evidence(
            settings,
            query="yeast assimilable nitrogen cider fermentation kinetics",
            article_count=3,
            retrieval_resources=resources,
            retrieval_trace=first_trace,
        )
        second = search_common_corpus_full_text_evidence(
            settings,
            query="yeast assimilable nitrogen cider fermentation kinetics",
            article_count=3,
            retrieval_resources=resources,
            retrieval_trace=second_trace,
        )
    finally:
        resources.close()

    assert [record.model_dump() for record in second] == [record.model_dump() for record in first]
    first_search = next(
        trace for trace in first_trace.models() if trace.stage == "full_text_search"
    )
    second_search = next(
        trace for trace in second_trace.models() if trace.stage == "full_text_search"
    )
    assert first_search.cache_miss_count == 1
    assert first_search.cache_hit_count == 0
    assert second_search.cache_hit_count == 1
    assert second_search.cache_miss_count == 0
    assert second_search.selected_article_count == len(second)


@pytest.fixture(autouse=True)
def explicit_semantic_gate_for_orchestration_tests(monkeypatch):
    """These tests isolate orchestration; filter behavior has its own failure tests."""

    class Gate:
        def __init__(self, _client, **_options):
            pass

        def filter_records(self, question, needs, records, **_options):
            return GlobalSemanticFilterResult(
                question=question,
                decisions=[
                    GlobalSemanticDecision(
                        candidate_id=record.record_id,
                        relevance="direct",
                        rationale="Fixture evidence for the orchestration test.",
                    )
                    for record in records
                ],
                selected_candidate_ids=[record.record_id for record in records],
                model="semantic-test",
                prompt_tokens=0,
                completion_tokens=0,
            )

    monkeypatch.setattr("app.services.workflows.ArgoGlobalSemanticEvidenceFilter", Gate)
