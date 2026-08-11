"""Select distinct articles from local hybrid retrieval with optional diversity."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.retrieval.article_ranking import ArticleRankingService
from app.retrieval.hybrid_search import HybridSearchService
from app.retrieval.lexical_search import LexicalSearchService
from app.retrieval.vector_search import QdrantLocalIndex, VectorSearchService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Scientific question or search query")
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--count", type=int, help="Number of distinct articles")
    parser.add_argument(
        "--diversity",
        choices=("none", "theme", "year", "journal", "balanced"),
    )
    parser.add_argument("--variant", action="append", dest="variants")
    parser.add_argument("--central-concept", action="append", dest="central_concepts")
    parser.add_argument("--exclude-article-id", action="append", dest="excluded_article_ids")
    parser.add_argument("--json", action="store_true", help="Print strict JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = settings_for_corpus(load_settings(args.config), CorpusScope.COMMON)
    database = Database(settings.paths.database_path)
    database.initialize()
    backend = SentenceTransformerBackend(settings)
    hybrid = HybridSearchService(
        settings,
        database,
        LexicalSearchService(settings, database),
        VectorSearchService(database, backend, QdrantLocalIndex(settings)),
    )
    ranking = ArticleRankingService(settings, database, hybrid)
    try:
        response = ranking.search(
            args.query,
            query_variants=args.variants,
            article_count=args.count,
            diversity_mode=args.diversity,
            central_concepts=args.central_concepts,
            exclude_article_ids=args.excluded_article_ids,
        )
        if args.json:
            print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))
            return 0

        print("MODE HORS LIGNE ACTIF" if settings.app.offline_mode else "MODE EN LIGNE")
        print(
            f"{response.selected_article_count}/{response.requested_article_count} "
            f"articles distincts | diversité={response.diversity_mode} | "
            f"durée={response.duration_seconds:.3f}s"
        )
        for article in response.articles:
            year = article.publication_year or "année inconnue"
            print(
                f"{article.rank}. {article.title} ({year}) — "
                f"score={article.adjusted_score:.4f} "
                f"(brut={article.base_score:.4f}, pénalité={article.diversity_penalty:.4f})"
            )
            print(
                f"   article={article.article_id} | fragments={article.top_chunk_ids} | "
                f"pages={article.page_ranges}"
            )
        return 0
    finally:
        ranking.close()


if __name__ == "__main__":
    raise SystemExit(main())
