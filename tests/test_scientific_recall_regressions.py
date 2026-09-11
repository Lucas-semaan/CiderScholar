"""Offline counterexamples for pre-validation evidence loss, never scientific gold labels."""

from contextlib import closing
from types import SimpleNamespace

import pytest

from app.chat_effort import AnswerEffort
from app.corpora import CorpusScope, corpus_paths
from app.database.sqlite import Database
from app.llm.article_evidence import EvidencePassageSelector
from app.retrieval.hypothesis_planning import deterministic_hypothesis_plan
from app.services.workflows import (
    _chat_retrieval_cache_signature,
    search_common_corpus_abstracts,
)
from app.updates.harvest import BibliographicHarvestStore
from app.updates.models import BibliographicRecord

RESULT = (
    "In apple cider fermentation the selected strain significantly increased volatile ester "
    "concentration under controlled temperature conditions in replicated laboratory trials."
)


def _article(database: Database, texts: list[str], doi: str | None = None) -> list[int]:
    database.save_article_and_chunks(
        {
            "id": "study",
            "sha256": "a" * 64,
            "doi": doi,
            "title": "Synthetic laboratory observations",
            "abstract": None,
            "authors": ["Synthetic Author"],
            "journal": "Synthetic Journal",
            "publication_year": 2025,
            "language": "en",
            "pdf_path": "synthetic.pdf",
            "validation_status": "indexed",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": index + 1,
                "page_end": index + 1,
                "chunk_index": index,
                "text": text,
                "token_count": len(text.split()),
                "embedding_status": "indexed",
            }
            for index, text in enumerate(texts)
        ],
    )
    with closing(database.connect()) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT id FROM chunks WHERE article_id='study' ORDER BY chunk_index"
            )
        ]


@pytest.mark.parametrize(
    "variant",
    [
        RESULT.replace("significantly", "not significantly"),
        RESULT.replace("increased", "decreased"),
        RESULT.replace("controlled temperature", "high temperature"),
        RESULT.replace("concentration", "concentration by 25%"),
    ],
    ids=["negation", "opposite-result", "different-condition", "numeric-result"],
)
def test_selector_preserves_scientifically_distinct_similar_passages(settings, variant):
    database = Database(settings.paths.database_path)
    database.initialize()
    ids = _article(
        database,
        [
            RESULT,
            "Apple cider treatment reduced measured turbidity.",
            "Residual sugar concentrations varied between apple cultivars.",
            variant,
        ],
    )
    selected = EvidencePassageSelector(settings, database).select(
        query="apple cider fermentation volatile ester",
        article_id="study",
        ranked_chunk_ids=ids,
        passage_count=4,
    )

    assert {passage.chunk_id for passage in selected} == set(ids)
    assert all(passage.page_start == ids.index(passage.chunk_id) + 1 for passage in selected)


def test_selector_still_defers_exact_repetition(settings):
    database = Database(settings.paths.database_path)
    database.initialize()
    ids = _article(
        database,
        [RESULT, "Turbidity decreased.", "Sugar consumption varied.", RESULT],
    )
    selected = EvidencePassageSelector(settings, database).select(
        query="apple cider fermentation volatile ester",
        article_id="study",
        ranked_chunk_ids=ids,
        passage_count=4,
    )

    assert len(selected) == 3
    assert sum(passage.text == RESULT for passage in selected) == 1


@pytest.mark.parametrize("length", [500, 501, 2001, 4000])
@pytest.mark.parametrize("effort", list(AnswerEffort))
def test_fallback_keeps_the_complete_supported_question(length, effort):
    prefix = "Dans le cidre, "
    question = prefix + "x" * (length - len(prefix) - len(" patuline")) + " patuline"
    result = deterministic_hypothesis_plan(question, effort=effort)

    assert result.used_fallback
    assert result.plan.interpreted_question == question
    assert result.plan.verification_needs[0].claim_to_verify == question
    assert result.plan.dense_queries(question)[0] == question
    assert result.prompt_tokens == result.completion_tokens == 0


def test_fallback_rejects_questions_outside_public_length():
    with pytest.raises(ValueError, match="between 2 and 4000"):
        deterministic_hypothesis_plan("cider " + "x" * 4000)


def test_found_abstract_survives_a_full_text_identity_without_selected_evidence(settings):
    database = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
    database.initialize()
    doi = "10.1000/synthetic-recall"
    _article(database, ["Unrelated laboratory bookkeeping."], doi=doi)
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        settings, themes={"aromes_procede": "cider esters"}, sources=["crossref"]
    )
    record_id = store.upsert_hit(
        run_id=run_id,
        theme="aromes_procede",
        rank=1,
        record=BibliographicRecord(
            source="crossref",
            source_id="synthetic-record",
            title="Apple cider fermentation volatile ester experiment",
            abstract=RESULT,
            doi=doi,
        ),
    )
    assert record_id is not None
    results = search_common_corpus_abstracts(
        settings, query="cider volatile ester", limit=5, max_vector_query_variants=0
    )

    assert [result.doi for result in results] == [doi]
    assert results[0].record_id == f"common-abstract:{record_id}"
    assert results[0].abstract == RESULT


@pytest.mark.parametrize("component", ["article_ranking", "evidence"])
def test_retrieval_cache_key_tracks_selection_settings(settings, component):
    resources = SimpleNamespace(corpus_fingerprint=lambda _settings: "a" * 64)

    def signature(config):
        return _chat_retrieval_cache_signature(
            config,
            resources,
            operation="full_text_search",
            query="cider fermentation",
            variants=["cider fermentation"],
            filters_limits={},
        )

    before = signature(settings)
    changed = settings.model_copy(deep=True)
    if component == "article_ranking":
        changed.article_ranking.top_chunks_per_article = 3
    else:
        changed.evidence.max_passage_characters = 8_000
    assert before.cache_key_sha256 != signature(changed).cache_key_sha256
    assert before.cache_key_sha256 == signature(settings).cache_key_sha256
