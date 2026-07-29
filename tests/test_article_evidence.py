from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import closing
from typing import Any

import pytest
from pydantic import ValidationError

from app.database.sqlite import Database
from app.llm.article_evidence import (
    ArticleEvidenceExtractor,
    EvidenceExtractionError,
    EvidencePassageSelector,
    EvidenceSourceValidationError,
)
from app.llm.contracts import (
    GenerationMetrics,
    GenerationResponse,
)
from app.models.evidence import ArticleEvidence


def _seed_article(database: Database, article_id: str = "article-1") -> list[int]:
    chunks = [
        (
            "Abstract",
            1,
            "Fermentation temperature was evaluated in a synthetic cider experiment.",
        ),
        (
            "Materials and methods",
            2,
            "Samples were incubated at 12 °C and 20 °C using a controlled protocol.",
        ),
        (
            "Results",
            3,
            "At 20 °C, ester concentration increased by 25% compared with 12 °C.",
        ),
        (
            "Discussion",
            4,
            "However, higher temperature did not increase every measured aroma compound.",
        ),
        (
            "Conclusion",
            5,
            "Fermentation temperature changed the synthetic cider aroma profile.",
        ),
        (
            "Other",
            6,
            "At 20 °C, ester concentration increased by 25% compared with 12 °C.",
        ),
    ]
    database.save_article_and_chunks(
        {
            "id": article_id,
            "sha256": ("a" if article_id == "article-1" else "b") * 64,
            "doi": None,
            "title": "Synthetic fermentation evidence",
            "abstract": chunks[0][2],
            "authors": ["Ada Test"],
            "journal": "Synthetic Journal",
            "publication_year": 2025,
            "language": "en",
            "pdf_path": f"data/pdf/{article_id}.pdf",
            "validation_status": "indexed",
            "source": "local",
        },
        [
            {
                "section": section,
                "page_start": page,
                "page_end": page,
                "chunk_index": index,
                "text": text,
                "token_count": len(text.split()),
                "embedding_status": "indexed",
            }
            for index, (section, page, text) in enumerate(chunks)
        ],
    )
    with closing(database.connect()) as connection:
        return [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM chunks WHERE article_id = ? ORDER BY chunk_index",
                (article_id,),
            )
        ]


def _metrics() -> GenerationMetrics:
    return GenerationMetrics(
        total_duration_seconds=1.0,
        load_duration_seconds=0.1,
        prompt_eval_count=100,
        prompt_eval_duration_seconds=0.2,
        eval_count=50,
        eval_duration_seconds=0.7,
    )


class SequenceChatClient:
    def __init__(self, contents: Sequence[str]) -> None:
        self.contents = list(contents)
        self.calls: list[tuple[Sequence[Mapping[str, str]], dict[str, Any]]] = []

    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        **options: Any,
    ) -> GenerationResponse:
        self.calls.append((messages, options))
        if not self.contents:
            raise AssertionError("unexpected LLM call")
        return GenerationResponse(
            model="chat-gpt-oss-20b",
            content=self.contents.pop(0),
            done_reason="stop",
            metrics=_metrics(),
        )


def _evidence_json(
    *,
    article_id: str,
    chunk_id: int,
    page: int,
    excerpt: str,
    extra: dict[str, object] | None = None,
) -> str:
    value: dict[str, object] = {
        "article_id": article_id,
        "relevance_score": 0.95,
        "question_addressed": "Effect of fermentation temperature on cider aroma.",
        "findings": [
            {
                "claim": "Higher temperature increased ester concentration.",
                "source_excerpt": excerpt,
                "page_start": page,
                "page_end": page,
                "chunk_id": str(chunk_id),
            }
        ],
        "topics": ["fermentation temperature", "esters"],
        "contradictions": [],
        "missing_information": [],
    }
    value.update(extra or {})
    return json.dumps(value)


def _query(database: Database, query_id: str = "query-1") -> None:
    database.create_query(
        query_id=query_id,
        original_query="How does fermentation temperature affect cider aroma?",
        expanded_queries=[],
        selected_article_ids=["article-1"],
        model_version="chat-gpt-oss-20b",
    )


def test_passage_selector_prioritizes_results_and_defers_methods(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    ids = _seed_article(database)
    selector = EvidencePassageSelector(settings, database)

    passages = selector.select(
        query="How does fermentation temperature affect cider aroma?",
        article_id="article-1",
        ranked_chunk_ids=[ids[1], ids[2], ids[3], ids[4], ids[5]],
    )

    assert 3 <= len(passages) <= 5
    assert passages[0].chunk_id == ids[2]
    assert {passage.section for passage in passages} >= {
        "Results",
        "Discussion",
        "Conclusion",
    }
    assert all(passage.section != "Materials and methods" for passage in passages)
    assert sum(passage.text.startswith("At 20") for passage in passages) == 1
    assert all(passage.article_id == "article-1" for passage in passages)


def test_passage_selector_rejects_chunk_from_another_article(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _seed_article(database)
    other_ids = _seed_article(database, "article-2")

    with pytest.raises(EvidenceSourceValidationError, match="different article"):
        EvidencePassageSelector(settings, database).select(
            query="temperature",
            article_id="article-1",
            ranked_chunk_ids=[other_ids[0]],
        )


def test_valid_evidence_is_persisted_and_resumed_without_llm(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    ids = _seed_article(database)
    _query(database)
    passages = EvidencePassageSelector(settings, database).select(
        query="fermentation temperature cider aroma",
        article_id="article-1",
        ranked_chunk_ids=[ids[2], ids[3], ids[4]],
        passage_count=3,
    )
    source = next(passage for passage in passages if passage.chunk_id == ids[2])
    llm = SequenceChatClient(
        [
            _evidence_json(
                article_id="article-1",
                chunk_id=source.chunk_id,
                page=source.page_start,
                excerpt=source.text,
            )
        ]
    )
    extractor = ArticleEvidenceExtractor(settings, database, llm)

    result = extractor.extract(
        query="How does fermentation temperature affect cider aroma?",
        article_id="article-1",
        passages=passages,
        query_id="query-1",
    )
    resumed = ArticleEvidenceExtractor(settings, database, SequenceChatClient([])).extract(
        query="How does fermentation temperature affect cider aroma?",
        article_id="article-1",
        passages=passages,
        query_id="query-1",
    )

    assert result.attempts == 1
    schema = llm.calls[0][1]["json_schema"]
    assert schema["properties"]["article_id"]["enum"] == ["article-1"]
    finding_schema = schema["properties"]["findings"]["items"]["properties"]
    assert source.text in finding_schema["source_excerpt"]["enum"]
    assert result.evidence.findings[0].source_excerpt == source.text
    assert database.article_evidence_run("query-1", "article-1")["state"] == "completed"
    assert database.load_article_evidence("query-1", "article-1") == result.evidence
    assert resumed.resumed_from_database is True
    assert resumed.attempts == 0


def test_tampered_pages_retry_once_then_accept_exact_sources(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    ids = _seed_article(database)
    _query(database)
    passages = EvidencePassageSelector(settings, database).select(
        query="temperature aroma",
        article_id="article-1",
        ranked_chunk_ids=[ids[2], ids[3], ids[4]],
        passage_count=3,
    )
    source = next(passage for passage in passages if passage.chunk_id == ids[2])
    invalid = _evidence_json(
        article_id="article-1",
        chunk_id=source.chunk_id,
        page=99,
        excerpt=source.text,
    )
    valid = _evidence_json(
        article_id="article-1",
        chunk_id=source.chunk_id,
        page=source.page_start,
        excerpt=source.text,
    )
    llm = SequenceChatClient([invalid, valid])

    result = ArticleEvidenceExtractor(settings, database, llm).extract(
        query="temperature aroma",
        article_id="article-1",
        passages=passages,
        query_id="query-1",
    )

    assert result.attempts == 2
    assert len(llm.calls) == 2
    assert "CORRECTION_REQUIRED" in llm.calls[1][0][1]["content"]
    assert result.evidence.findings[0].page_start == source.page_start


def test_invented_doi_and_excerpt_are_never_persisted(settings, caplog) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    ids = _seed_article(database)
    _query(database)
    passages = EvidencePassageSelector(settings, database).select(
        query="temperature aroma",
        article_id="article-1",
        ranked_chunk_ids=[ids[2], ids[3], ids[4]],
        passage_count=3,
    )
    invented = _evidence_json(
        article_id="article-1",
        chunk_id=ids[2],
        page=3,
        excerpt="This sentence does not exist in SQLite.",
        extra={"doi": "10.9999/invented"},
    )

    with pytest.raises(EvidenceExtractionError, match="source-valid"):
        ArticleEvidenceExtractor(
            settings, database, SequenceChatClient([invented, invented])
        ).extract(
            query="temperature aroma",
            article_id="article-1",
            passages=passages,
            query_id="query-1",
        )

    run = database.article_evidence_run("query-1", "article-1")
    assert run["state"] == "failed"
    with closing(database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
    assert "10.9999/invented" not in caplog.text
    with pytest.raises(ValidationError):
        ArticleEvidence.model_validate_json(invented)
