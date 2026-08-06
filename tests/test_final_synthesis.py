from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import closing
from typing import Any

import pytest
from pydantic import ValidationError

from app.corpora import CorpusScope
from app.database.sqlite import Database
from app.llm.contracts import GenerationMetrics, GenerationResponse
from app.llm.final_synthesis import HierarchicalSynthesisService
from app.models.evidence import ArticleEvidence
from app.models.synthesis import CitedStatement

QUESTION = "How does fermentation temperature affect cider volatile compounds?"


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
    def __init__(self, contents: Sequence[str | Exception]) -> None:
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
        value = self.contents.pop(0)
        if isinstance(value, Exception):
            raise value
        return GenerationResponse(
            model="chat-gpt-oss-20b",
            content=value,
            done_reason="stop",
            metrics=_metrics(),
        )


def _seed_query(database: Database, article_count: int = 2) -> dict[str, str]:
    article_ids = [f"article-{index}" for index in range(1, article_count + 1)]
    chunks: dict[str, tuple[int, str, int]] = {}
    for index, article_id in enumerate(article_ids, start=1):
        page = 2 if index == 1 else 5
        direction = "increased by 25%" if index == 1 else "decreased by 10%"
        text = f"At 20 °C, ester concentration {direction} compared with 12 °C."
        database.save_article_and_chunks(
            {
                "id": article_id,
                "sha256": chr(96 + index) * 64,
                "doi": f"10.1000/sqlite-{index}",
                "title": f"Synthetic cider study {index}",
                "abstract": "A synthetic local test article.",
                "authors": [f"Author {index}"],
                "journal": "SQLite Journal",
                "publication_year": 2024 + index,
                "language": "en",
                "pdf_path": f"data/pdf/{article_id}.pdf",
                "validation_status": "indexed",
                "source": "local",
            },
            [
                {
                    "section": "Results",
                    "page_start": page,
                    "page_end": page,
                    "chunk_index": 0,
                    "text": text,
                    "token_count": len(text.split()),
                    "embedding_status": "indexed",
                }
            ],
        )
        with closing(database.connect()) as connection:
            chunk_id = int(
                connection.execute(
                    "SELECT id FROM chunks WHERE article_id = ?", (article_id,)
                ).fetchone()[0]
            )
        chunks[article_id] = (chunk_id, text, page)

    database.create_query(
        query_id="query-synthesis",
        original_query=QUESTION,
        expanded_queries=[],
        selected_article_ids=article_ids,
        model_version="chat-gpt-oss-20b",
    )
    for article_id in article_ids:
        chunk_id, text, page = chunks[article_id]
        database.start_article_evidence_run(
            query_id="query-synthesis",
            article_id=article_id,
            selected_chunk_ids=[chunk_id],
        )
        database.save_article_evidence(
            query_id="query-synthesis",
            evidence=ArticleEvidence.model_validate(
                {
                    "article_id": article_id,
                    "relevance_score": 0.9,
                    "question_addressed": QUESTION,
                    "findings": [
                        {
                            "claim": text,
                            "source_excerpt": text,
                            "page_start": page,
                            "page_end": page,
                            "chunk_id": str(chunk_id),
                        }
                    ],
                    "topics": ["fermentation temperature", "esters"],
                    "contradictions": [],
                    "missing_information": ["No sensory panel was reported."],
                }
            ),
            selected_chunk_ids=[chunk_id],
        )
    return {
        str(row["article_id"]): str(row["evidence_id"])
        for row in database.evidence_records_for_query("query-synthesis")
    }


def _plan_json(article_ids: list[str]) -> str:
    return json.dumps(
        {
            "themes": [
                {
                    "theme_id": "theme-1",
                    "label": "Temperature and esters",
                    "article_ids": article_ids,
                }
            ]
        }
    )


def _theme_json(article_ids: list[str], evidence_ids: list[str]) -> str:
    multi = len(article_ids) > 1
    shared = evidence_ids if multi else [evidence_ids[0]]
    return json.dumps(
        {
            "theme_id": "theme-1",
            "label": "Temperature and esters" if multi else "fermentation temperature",
            "article_ids": article_ids,
            "summary": [
                {
                    "statement": "Fermentation temperature affected ester concentration.",
                    "evidence_ids": shared,
                }
            ],
            "convergent_results": (
                [
                    {
                        "statement": "Both studies reported a temperature effect.",
                        "evidence_ids": shared,
                    }
                ]
                if multi
                else []
            ),
            "contradictory_results": (
                [
                    {
                        "statement": "The reported directions of change differed.",
                        "evidence_ids": shared,
                    }
                ]
                if multi
                else []
            ),
            "quantitative_results": [
                {
                    "statement": "One study reported a 25% change.",
                    "evidence_ids": [evidence_ids[0]],
                }
            ],
            "missing_information": ["Sensory outcomes were not established."],
        }
    )


def _final_json(evidence_ids: list[str]) -> str:
    multi = len(evidence_ids) > 1
    shared = evidence_ids if multi else [evidence_ids[0]]
    return json.dumps(
        {
            "direct_answer": [
                {
                    "statement": "Temperature changed the measured ester concentration.",
                    "evidence_ids": shared,
                }
            ],
            "consensus": (
                [
                    {
                        "statement": "The studies agree that temperature had an effect.",
                        "evidence_ids": shared,
                    }
                ]
                if multi
                else []
            ),
            "convergent_results": [],
            "contradictory_results": (
                [
                    {
                        "statement": "The direction differed between the studies.",
                        "evidence_ids": shared,
                    }
                ]
                if multi
                else []
            ),
            "quantitative_results": [
                {
                    "statement": "A 25% change was reported in one article.",
                    "evidence_ids": [evidence_ids[0]],
                }
            ],
            "missing_information": (
                [] if multi else ["Cross-article consensus cannot be assessed."]
            ),
        }
    )


def test_hierarchical_synthesis_renders_only_sqlite_citations(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    evidence = _seed_query(database)
    article_ids = list(evidence)
    evidence_ids = list(evidence.values())
    llm = SequenceChatClient(
        [
            _plan_json(article_ids),
            _theme_json(article_ids, evidence_ids),
            _final_json(evidence_ids),
        ]
    )

    execution = HierarchicalSynthesisService(settings, database, llm).synthesize(
        query_id="query-synthesis"
    )

    assert execution.llm_calls == 3
    assert execution.result.cited_evidence_ids == evidence_ids
    assert "[Corpus commun · article-1, p. 2]" in execution.result.answer_markdown
    assert "[Corpus commun · article-2, p. 5]" in execution.result.answer_markdown
    assert "One study reported a 25% change." in execution.result.answer_markdown
    assert "10.1000/sqlite-1" in execution.result.answer_markdown
    assert len(execution.result.bibliography) == 2
    assert database.synthesis_run("query-synthesis")["state"] == "completed"
    assert all(
        "10.1000/sqlite" not in message["content"]
        for messages, _options in llm.calls
        for message in messages
    )
    plan_schema = llm.calls[0][1]["json_schema"]
    article_enum = plan_schema["properties"]["themes"]["items"]["properties"]["article_ids"][
        "items"
    ]["enum"]
    assert article_enum == article_ids


def test_synthesis_preserves_common_origin_in_citations_and_bibliography(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    evidence = _seed_query(database, article_count=1)
    article_ids = list(evidence)
    evidence_ids = list(evidence.values())
    llm = SequenceChatClient(
        [
            _plan_json(article_ids),
            _theme_json(article_ids, evidence_ids),
            _final_json(evidence_ids),
        ]
    )

    result = (
        HierarchicalSynthesisService(
            settings,
            database,
            llm,
            scope=CorpusScope.COMMON,
        )
        .synthesize(query_id="query-synthesis")
        .result
    )

    assert "[Corpus commun · article-1, p. 2]" in result.answer_markdown
    assert result.bibliography[0].scope is CorpusScope.COMMON


def test_invalid_evidence_id_is_retried_before_theme_persistence(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    evidence = _seed_query(database, article_count=1)
    evidence_id = next(iter(evidence.values()))
    invalid = json.loads(_theme_json(["article-1"], [evidence_id]))
    invalid["summary"][0]["evidence_ids"] = ["fabricated-evidence"]
    llm = SequenceChatClient(
        [
            json.dumps(invalid),
            _theme_json(["article-1"], [evidence_id]),
            _final_json([evidence_id]),
        ]
    )

    execution = HierarchicalSynthesisService(settings, database, llm).synthesize(
        query_id="query-synthesis"
    )

    assert execution.llm_calls == 3
    assert database.theme_synthesis_run("query-synthesis", "theme-1")["state"] == ("completed")
    assert "fabricated-evidence" not in execution.result.answer_markdown


def test_interrupted_final_resumes_completed_theme_and_final_result(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    evidence = _seed_query(database, article_count=1)
    evidence_id = next(iter(evidence.values()))
    first_llm = SequenceChatClient(
        [
            _theme_json(["article-1"], [evidence_id]),
            RuntimeError("synthetic interruption"),
        ]
    )

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        HierarchicalSynthesisService(settings, database, first_llm).synthesize(
            query_id="query-synthesis"
        )

    assert database.synthesis_run("query-synthesis")["state"] == "failed"
    assert database.theme_synthesis_run("query-synthesis", "theme-1")["state"] == ("completed")
    resumed_llm = SequenceChatClient([_final_json([evidence_id])])
    resumed = HierarchicalSynthesisService(settings, database, resumed_llm).synthesize(
        query_id="query-synthesis"
    )

    assert resumed.llm_calls == 1
    assert resumed.resumed_theme_count == 1
    with closing(database.connect()) as connection:
        connection.execute(
            "UPDATE articles SET title = ? WHERE id = ?",
            ("Title updated only in SQLite", "article-1"),
        )
        connection.commit()
    completed = HierarchicalSynthesisService(settings, database, SequenceChatClient([])).synthesize(
        query_id="query-synthesis"
    )
    assert completed.resumed_from_database is True
    assert completed.llm_calls == 0
    assert completed.result.bibliography[0].title == "Title updated only in SQLite"
    assert "Title updated only in SQLite" in completed.result.answer_markdown
    assert (
        "Title updated only in SQLite"
        in database.synthesis_run("query-synthesis")["answer_markdown"]
    )


def test_single_article_cannot_claim_cross_article_consensus(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    evidence = _seed_query(database, article_count=1)
    evidence_id = next(iter(evidence.values()))
    invalid_final = json.loads(_final_json([evidence_id]))
    invalid_final["consensus"] = [
        {
            "statement": "A consensus exists.",
            "evidence_ids": [evidence_id],
        }
    ]
    llm = SequenceChatClient(
        [
            _theme_json(["article-1"], [evidence_id]),
            json.dumps(invalid_final),
            _final_json([evidence_id]),
        ]
    )

    execution = HierarchicalSynthesisService(settings, database, llm).synthesize(
        query_id="query-synthesis"
    )

    assert execution.llm_calls == 3
    assert execution.result.final.consensus == []
    assert "Cross-article consensus cannot be assessed." in (
        execution.result.final.missing_information
    )


def test_model_generated_doi_and_citation_text_are_forbidden() -> None:
    with pytest.raises(ValidationError, match="DOI"):
        CitedStatement(
            statement="The result is identified by 10.9999/invented.",
            evidence_ids=["evidence-1"],
        )
    with pytest.raises(ValidationError, match="citation text"):
        CitedStatement(
            statement="The result increased [article-1, p. 2].",
            evidence_ids=["evidence-1"],
        )
