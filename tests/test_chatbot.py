from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.database.sqlite import Database
from app.llm.argo_client import ArgoQuotaError
from app.models.chatbot import ChatbotFacetDraft, ChatEvidencePassage, ChatEvidenceRecord
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
    monkeypatch.setattr(
        "app.services.workflows.search_common_corpus_full_text_evidence",
        lambda *_args, **_kwargs: [],
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
