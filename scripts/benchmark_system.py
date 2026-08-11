"""Run the reproducible offline retrieval and traceability benchmark."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.evaluation.benchmark import (
    BenchmarkRunner,
    build_demo_cases,
    load_evaluation_cases,
    write_benchmark_outputs,
)
from app.ingestion.embeddings import SentenceTransformerBackend
from app.retrieval.article_ranking import ArticleRankingService
from app.retrieval.hybrid_search import HybridSearchService
from app.retrieval.lexical_search import LexicalSearchService
from app.retrieval.vector_search import QdrantLocalIndex, VectorSearchService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "cases",
        nargs="?",
        type=Path,
        help="JSON array or object containing labelled evaluation cases",
    )
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument(
        "--demo-corpus",
        action="store_true",
        help="Generate the three labelled cases from the local synthetic demo corpus",
    )
    parser.add_argument("--top-k", type=int, default=20, help="Evaluation depth")
    parser.add_argument(
        "--diversity",
        choices=("none", "theme", "year", "journal", "balanced"),
        default="balanced",
    )
    parser.add_argument("--markdown-output", type=Path, help="Markdown report path")
    parser.add_argument("--json-output", type=Path, help="Structured JSON report path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.demo_corpus == (args.cases is not None):
        parser.error("provide exactly one cases JSON path or --demo-corpus")

    settings = settings_for_corpus(load_settings(args.config), CorpusScope.COMMON)
    settings.paths.create()
    database = Database(settings.paths.database_path)
    database.initialize()
    try:
        cases = (
            build_demo_cases(database) if args.demo_corpus else load_evaluation_cases(args.cases)
        )
    except Exception as exc:
        print(f"Cas d’évaluation invalides: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    markdown_path = args.markdown_output or (
        settings.paths.exports_dir / f"benchmark-{timestamp}.md"
    )
    json_path = args.json_output or markdown_path.with_suffix(".json")

    backend = SentenceTransformerBackend(settings)
    hybrid = HybridSearchService(
        settings,
        database,
        LexicalSearchService(settings, database),
        VectorSearchService(database, backend, QdrantLocalIndex(settings)),
    )
    ranking = ArticleRankingService(settings, database, hybrid)
    try:
        report = BenchmarkRunner(
            database,
            ranking,
            model_name=settings.embeddings.model_name,
            top_k=args.top_k,
            diversity_mode=args.diversity,
        ).run(cases)
        written_markdown, written_json = write_benchmark_outputs(
            report,
            markdown_path=markdown_path,
            json_path=json_path,
        )
    except Exception as exc:
        print(f"Benchmark échoué: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        ranking.close()

    print("MODE HORS LIGNE ACTIF" if settings.app.offline_mode else "MODE EN LIGNE")
    print(
        f"cas={report.case_count} | P@{report.top_k}={report.aggregate.precision_at_k:.4f} | "
        f"R@{report.top_k}={report.aggregate.recall_at_k:.4f} | "
        f"MRR={report.aggregate.mean_reciprocal_rank:.4f} | "
        f"nDCG={report.aggregate.ndcg_at_k:.4f} | durée={report.duration_seconds:.3f}s"
    )
    print(f"rapport_markdown={written_markdown}")
    if written_json is not None:
        print(f"rapport_json={written_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
