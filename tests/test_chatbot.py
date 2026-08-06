from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.database.sqlite import Database
from app.llm.argo_client import (
    ArgoQuotaError,
    ArgoScientificValidationError,
    ScientificValidationReason,
)
from app.models.chatbot import ChatbotFacetDraft, ChatEvidencePassage, ChatEvidenceRecord
from app.retrieval.coverage_assessment import (
    AxisCoverageAssessment,
    CoverageAssessmentResult,
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
    acquire_common_full_text_for_chat,
    answer_chatbot,
    search_common_corpus_abstracts,
    search_common_corpus_full_text_evidence,
)
from app.updates.models import BibliographicRecord
from app.updates.vector_index import BibliographicHybridResult


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


def test_chatbot_returns_only_cited_source_cards() -> None:
    local = [_local(1)]
    merged, _ = merge_chatbot_candidates(local, [_external("fresh", "10.1000/external")])

    sources = chatbot_sources(merged, [merged[-1].record_id])

    assert len(sources) == 1
    assert sources[0].origin == "external_api"
    assert sources[0].evidence_level == "abstract"
    assert sources[0].doi == "10.1000/external"


def test_chatbot_full_text_source_persists_chunks_and_pages_for_follow_up() -> None:
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
    )

    assert len(sources) == 1
    assert sources[0].evidence_level == "full_text"
    assert sources[0].article_id == "article-1"
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
        def __init__(self, _client):
            pass

        def answer(self, _question, records, **_kwargs):
            captured["records"] = records
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
        assert "stabilité protéique" in question
        decisions = [
            CandidateSemanticDecision(
                candidate_id=record.record_id,
                relevance="direct" if record.record_id == relevant.record_id else "irrelevant",
                rationale=(
                    "Correspondance mécanistique multilingue."
                    if record.record_id == relevant.record_id
                    else "Le dosage de patuline ne traite pas la stabilité protéique."
                ),
            )
            for record in evidence
        ]
        semantic = SemanticFilterResult(
            question=question,
            axes=[AxisSemanticAssessment(axis_key=axis.key, decisions=decisions) for axis in axes],
            selected_candidate_ids=[relevant.record_id],
            model="semantic-test",
            prompt_tokens=7,
            completion_tokens=5,
        )
        coverage = CoverageAssessmentResult(
            question=question,
            axes=[
                AxisCoverageAssessment(
                    axis_key=axis.key,
                    status="covered",
                    supporting_candidate_ids=[relevant.record_id],
                    assessment="L'axe est directement documenté.",
                )
                for axis in axes
            ],
            model="coverage-test",
            prompt_tokens=11,
            completion_tokens=3,
        )
        return semantic, coverage

    class FakeEvidenceService:
        def __init__(self, _client):
            pass

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
        "app.services.workflows._semantic_filter_and_coverage",
        fake_filter_and_coverage,
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
    assert captured["coverage_notes"] == []
    assert result.prompt_tokens == 20
    assert result.completion_tokens == 9
    assert [source.record_id for source in result.sources] == [relevant.record_id]
    assert progress_stages == [
        "planning",
        "search",
        "reranking",
        "evidence_selection",
        "coverage",
        "generation",
    ]


def test_answer_chatbot_runs_only_one_targeted_follow_up_for_an_uncovered_axis(
    settings,
    monkeypatch,
) -> None:
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

    class FakeArgoClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    def fake_abstract_search(*_args, **options):
        calls["abstract"] += 1
        if calls["abstract"] == 1:
            return [initial]
        captured["follow_up_query"] = options["query"]
        return [supplemental]

    def fake_full_text_search(*_args, **options):
        calls["full_text"] += 1
        if calls["full_text"] == 2:
            captured["follow_up_axis_queries"] = options["axis_queries"]
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
                    status="partial" if is_first_pass else "covered",
                    supporting_candidate_ids=[initial.record_id],
                    assessment=(
                        "Le mécanisme manque."
                        if is_first_pass
                        else "Le mécanisme est maintenant couvert."
                    ),
                    missing_information=(
                        ["Mécanismes d'agrégation protéines-polyphénols"] if is_first_pass else []
                    ),
                    suggested_queries=(
                        ["apple juice protein polyphenol aggregation haze"] if is_first_pass else []
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
        def __init__(self, _client):
            pass

        @staticmethod
        def _result(records, **options):
            captured["records"] = records
            captured["coverage_notes"] = options["coverage_notes"]
            return SimpleNamespace(
                answer_markdown="Synthèse complétée.",
                cited_evidence_ids=[f"{supplemental.record_id}:abstract"],
                source_record_ids=[supplemental.record_id],
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
    )
    monkeypatch.setattr("app.services.workflows.ArgoClient", FakeArgoClient)
    monkeypatch.setattr(
        "app.services.workflows.CiderEvidenceRagService",
        FakeEvidenceService,
    )

    result = answer_chatbot(
        settings,
        Database(settings.paths.database_path),
        message="Fais un état de l'art sur la stabilité protéique des jus de pomme.",
        history=[],
        use_external_sources=False,
    )

    assert calls == {"abstract": 2, "full_text": 2, "assessment": 2}
    assert captured["follow_up_query"] == "apple juice protein polyphenol aggregation haze"
    assert captured["follow_up_axis_queries"]
    assert {record.record_id for record in captured["records"]} == {
        initial.record_id,
        supplemental.record_id,
    }
    assert captured["coverage_notes"] == []
    assert result.prompt_tokens == 20
    assert result.completion_tokens == 11


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
        def __init__(self, _client):
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


def test_answer_chatbot_returns_extracts_when_argo_synthesis_is_invalid(
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
        def __init__(self, _client):
            pass

        def answer(self, *_args, **_kwargs):
            raise ArgoScientificValidationError(
                "unsupported numeric claim",
                reason=ScientificValidationReason.UNSUPPORTED_NUMERIC_CLAIM,
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

    assert result.generation_status == "extractive_fallback"
    assert result.diagnostic_code == "unsupported_numeric_claim"
    assert result.model == "deterministic-evidence-fallback"
    assert [source.record_id for source in result.sources] == [candidate.record_id]
    assert "passages les mieux classés" in result.answer_markdown


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
        message="Question dont le retrieval est simulé vide",
        history=[],
        use_external_sources=False,
    )

    assert result.generation_status == "diagnostic_only"
    assert result.diagnostic_code == "retrieval_no_qualified_evidence"
    assert result.sources == []
    assert "anomalie de retrieval" in result.answer_markdown


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
        message="Question avec panne de retrieval simulée",
        history=[],
        use_external_sources=False,
    )

    assert result.generation_status == "diagnostic_only"
    assert result.diagnostic_code == "retrieval_unavailable"
    assert any("RuntimeError" in warning for warning in result.warnings)


def test_answer_chatbot_exposes_retrieved_evidence_when_semantic_filter_selects_nothing(
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

    def reject_all(
        _settings,
        *,
        question,
        axes,
        evidence,
        on_argo_reserved,
        on_coverage_started,
    ):
        del evidence, on_argo_reserved
        on_coverage_started()
        semantic = SemanticFilterResult(
            question=question,
            axes=[
                AxisSemanticAssessment(
                    axis_key=axis.key,
                    decisions=[
                        CandidateSemanticDecision(
                            candidate_id=candidate.record_id,
                            relevance="irrelevant",
                            rationale="Rejet simulé pour tester le repli.",
                        )
                    ],
                )
                for axis in axes
            ],
            selected_candidate_ids=[],
            model="semantic-test",
            prompt_tokens=1,
            completion_tokens=1,
        )
        coverage = CoverageAssessmentResult(
            question=question,
            axes=[
                AxisCoverageAssessment(
                    axis_key=axis.key,
                    status="missing",
                    supporting_candidate_ids=[],
                    assessment="Couverture rejetée par simulation.",
                )
                for axis in axes
            ],
            model="coverage-test",
            prompt_tokens=1,
            completion_tokens=1,
        )
        return semantic, coverage

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
        "app.services.workflows._semantic_filter_and_coverage",
        reject_all,
    )
    monkeypatch.setattr(
        "app.services.workflows._coverage_follow_up_queries",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr("app.services.workflows.ArgoClient", FakeArgoClient)

    result = answer_chatbot(
        settings,
        Database(settings.paths.database_path),
        message="Question technique avec rejet sémantique simulé",
        history=[],
        use_external_sources=False,
    )

    assert result.generation_status == "extractive_fallback"
    assert result.diagnostic_code == "semantic_filter_empty"
    assert [source.record_id for source in result.sources] == [candidate.record_id]


def test_answer_chatbot_uses_faceted_drafts_for_multi_axis_research(
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
    draft = ChatbotFacetDraft(
        key="aroma",
        label="Arômes et composés volatils",
        query="Axe arômes",
        answer_markdown="Brouillon cité.",
        cited_evidence_ids=[evidence_id],
        source_record_ids=[candidate.record_id],
    )

    class FakeArgoClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    class FakeEvidenceService:
        def __init__(self, _client):
            pass

        def answer(self, *_args, **_kwargs):
            raise AssertionError("a multi-axis query must use faceted generation")

        def answer_faceted(self, _question, records, *, facets, **_kwargs):
            captured["records"] = records
            captured["facets"] = facets
            return SimpleNamespace(
                answer_markdown="Réponse finale assemblée.",
                cited_evidence_ids=[evidence_id],
                source_record_ids=[candidate.record_id],
                model="test-model",
                prompt_tokens=30,
                completion_tokens=20,
                facet_drafts=[draft],
            )

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

    assert {facet.key for facet in captured["facets"]} == {
        "aroma",
        "structure",
        "evolution",
    }
    assert set(captured["axis_queries"]) == {"aroma", "structure", "evolution"}
    assert captured["records"][0].doi == "10.1000/apple-brandy"
    assert result.answer_markdown == "Réponse finale assemblée."
    assert result.facet_drafts == [draft]


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

    monkeypatch.setattr(
        "app.services.workflows.FullTextHarvestService",
        FakeHarvestService,
    )
    monkeypatch.setattr("app.services.workflows.index_pending_chunks", fake_index)
    candidate = _local(1).model_copy(update={"record_id": "common-abstract:record-1"})

    article_ids, warnings = acquire_common_full_text_for_chat(settings, [candidate])

    assert article_ids == ["global-article"]
    assert warnings == []
    assert captured["database_path"] == settings.paths.common_database_path
    assert captured["indexed_database_path"] == settings.paths.common_database_path
    assert captured["pdf_dir"] == settings.paths.common_pdf_dir
    assert captured["indexed_article_ids"] == ["global-article"]
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

    results = search_common_corpus_abstracts(settings, query=title, limit=5)
    sources = chatbot_sources(results, [results[0].record_id])

    assert [result.record_id for result in results] == ["common:common-biogas"]
    assert results[0].title == title
    assert sources[0].scope.value == "common"
    assert sources[0].providers == ["corpus-base"]


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

    records = search_common_corpus_full_text_evidence(
        settings,
        query="yeast assimilable nitrogen fermentation kinetics",
        article_count=3,
    )

    assert [record.article_id for record in records] == ["full-text-only"]
    assert records[0].evidence_level == "full_text"
    assert records[0].passages[0].page_start == 6
    assert records[0].passages[0].page_end == 7
    assert records[0].passages[0].chunk_id is not None
