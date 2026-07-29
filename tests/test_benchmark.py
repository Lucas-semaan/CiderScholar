from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.database.sqlite import Database
from app.evaluation.benchmark import (
    BenchmarkReport,
    BenchmarkRunner,
    EvaluationCase,
    build_demo_cases,
    corpus_fingerprint,
    load_evaluation_cases,
    render_markdown_report,
    write_benchmark_outputs,
)
from app.models.evidence import ArticleEvidence
from app.models.synthesis import CitedStatement, FinalSynthesis


def _seed_article(database: Database, article_id: str, title: str, text: str) -> int:
    database.save_article_and_chunks(
        {
            "id": article_id,
            "sha256": f"{sum(title.encode('utf-8')):064x}"[-64:],
            "doi": None,
            "title": title,
            "abstract": text,
            "authors": ["Local Test"],
            "journal": "Synthetic Journal",
            "publication_year": 2026,
            "language": "fr",
            "pdf_path": f"data/pdf/{article_id}.pdf",
            "validation_status": "indexed",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": 2,
                "page_end": 2,
                "chunk_index": 0,
                "text": text,
                "token_count": len(text.split()),
                "embedding_status": "indexed",
            }
        ],
    )
    return database.article_chunk_ids(article_id)[0]


class StaticMonitor:
    peak_process_rss_gb = 0.5
    peak_system_used_gb = 4.0

    def __enter__(self) -> StaticMonitor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class StubRanker:
    def __init__(self, articles: list[SimpleNamespace]) -> None:
        self.articles = articles
        self.calls: list[str] = []
        self.options: list[dict[str, object]] = []

    def search(self, query: str, **_options: object) -> SimpleNamespace:
        self.calls.append(query)
        self.options.append(_options)
        return SimpleNamespace(articles=self.articles)


def _persist_traceable_synthesis(
    database: Database, *, question: str, article_id: str, chunk_id: int
) -> str:
    query_id = "benchmark-query"
    database.create_query(
        query_id=query_id,
        original_query=question,
        expanded_queries=[],
        selected_article_ids=[article_id],
        model_version="chat-gpt-oss-20b",
    )
    database.start_article_evidence_run(
        query_id=query_id,
        article_id=article_id,
        selected_chunk_ids=[chunk_id],
    )
    evidence = ArticleEvidence.model_validate(
        {
            "article_id": article_id,
            "relevance_score": 1.0,
            "question_addressed": question,
            "findings": [
                {
                    "claim": "Les polyphénols diminuent pendant le stockage.",
                    "source_excerpt": "Les polyphénols diminuent pendant le stockage.",
                    "page_start": 2,
                    "page_end": 2,
                    "chunk_id": str(chunk_id),
                }
            ],
            "topics": ["polyphénols", "stockage"],
            "contradictions": [],
            "missing_information": [],
        }
    )
    database.save_article_evidence(
        query_id=query_id,
        evidence=evidence,
        selected_chunk_ids=[chunk_id],
    )
    evidence_id = str(database.evidence_records_for_query(query_id)[0]["evidence_id"])
    final = FinalSynthesis(
        direct_answer=[
            CitedStatement(
                statement="Les polyphénols diminuent pendant le stockage.",
                evidence_ids=[evidence_id],
            )
        ],
        consensus=[],
        convergent_results=[],
        contradictory_results=[],
        quantitative_results=[],
        missing_information=[],
    )
    database.start_synthesis_run(query_id=query_id, model_version="chat-gpt-oss-20b")
    database.save_final_synthesis(
        query_id=query_id,
        synthesis=final,
        answer_markdown="Réponse locale.",
        cited_evidence_ids=[evidence_id],
    )
    return query_id


def test_evaluation_case_is_strict_and_disjoint() -> None:
    with pytest.raises(ValidationError, match="disjoint"):
        EvaluationCase(
            question="Question",
            expected_article_ids=["article-1"],
            acceptable_article_ids=["article-1"],
        )
    with pytest.raises(ValidationError, match="duplicated"):
        EvaluationCase(
            question="Question",
            expected_article_ids=["article-1", "article-1"],
        )


def test_load_evaluation_cases_accepts_array_and_envelope(tmp_path: Path) -> None:
    case = {
        "question": "Question locale",
        "expected_article_ids": ["article-1"],
        "acceptable_article_ids": [],
        "expected_concepts": ["concept"],
    }
    array_path = tmp_path / "array.json"
    envelope_path = tmp_path / "envelope.json"
    array_path.write_text(json.dumps([case]), encoding="utf-8")
    envelope_path.write_text(json.dumps({"cases": [case]}), encoding="utf-8")

    assert load_evaluation_cases(array_path)[0].question == "Question locale"
    assert load_evaluation_cases(envelope_path)[0].expected_concepts == ["concept"]


def test_benchmark_runner_measures_retrieval_traceability_and_outputs(
    settings, tmp_path: Path
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    question = "Quelle est la stabilité des polyphénols pendant le stockage ?"
    source_text = "Les polyphénols diminuent pendant le stockage."
    chunk_id = _seed_article(database, "article-1", "Stabilité des polyphénols", source_text)
    query_id = _persist_traceable_synthesis(
        database,
        question=question,
        article_id="article-1",
        chunk_id=chunk_id,
    )
    ranker = StubRanker(
        [
            SimpleNamespace(
                article_id="article-1",
                title="Stabilité des polyphénols",
                abstract=source_text,
                top_chunk_ids=[chunk_id],
            )
        ]
    )
    report = BenchmarkRunner(
        database,
        ranker,
        model_name="fake-e5",
        top_k=2,
        diversity_mode="none",
        monitor_factory=StaticMonitor,
    ).run(
        [
            EvaluationCase(
                question=question,
                expected_article_ids=["article-1"],
                expected_concepts=["polyphénols", "stockage"],
                query_id=query_id,
            )
        ]
    )

    assert ranker.calls == [question]
    assert "central_concepts" not in ranker.options[0]
    assert report.aggregate.precision_at_k == 0.5
    assert report.aggregate.recall_at_k == 1.0
    assert report.aggregate.mean_reciprocal_rank == 1.0
    assert report.aggregate.ndcg_at_k == 1.0
    assert report.aggregate.concept_recall == 1.0
    assert report.traceability.traceable_citation_rate == 1.0
    assert report.traceability.unsupported_assertion_rate == 0.0
    assert report.peak_process_rss_gb == 0.5
    assert report.corpus_version == corpus_fingerprint(database)

    markdown = render_markdown_report(report)
    assert "Précision@2" in markdown
    assert "100.00%" in markdown
    markdown_path, json_path = write_benchmark_outputs(
        report,
        markdown_path=tmp_path / "report.md",
        json_path=tmp_path / "report.json",
    )
    assert markdown_path.read_text(encoding="utf-8").startswith("# Rapport")
    assert json_path is not None
    assert (
        BenchmarkReport.model_validate_json(json_path.read_text(encoding="utf-8")).corpus_version
        == report.corpus_version
    )


def test_build_demo_cases_maps_the_three_generated_titles(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    titles = (
        "Temperature and Aroma Formation in Synthetic Cider Fermentation",
        "Nitrogen Availability and Synthetic Yeast Kinetics",
        "Stockage local et stabilité fictive des polyphénols",
    )
    for index, title in enumerate(titles, start=1):
        _seed_article(database, f"demo-{index}", title, f"Synthetic result {index}")

    cases = build_demo_cases(database)

    assert len(cases) == 3
    assert [case.expected_article_ids[0] for case in cases] == [
        "demo-1",
        "demo-2",
        "demo-3",
    ]


def test_build_demo_cases_fails_if_corpus_is_incomplete(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()

    with pytest.raises(ValueError, match="incomplete"):
        build_demo_cases(database)
