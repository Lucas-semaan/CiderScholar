"""Run weighted RRF fusion over local SQLite FTS5 and embedded Qdrant."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.retrieval.hybrid_search import HybridSearchService
from app.retrieval.lexical_search import LexicalSearchService
from app.retrieval.vector_search import QdrantLocalIndex, VectorSearchService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Scientific question or search query")
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--limit", type=int, help="Maximum fused chunks")
    parser.add_argument("--candidate-limit", type=int, help="Candidates per channel")
    parser.add_argument("--variant", action="append", dest="variants")
    parser.add_argument("--article-id", action="append", dest="article_ids")
    parser.add_argument("--section", action="append", dest="sections")
    parser.add_argument("--lexical-mode", choices=("any", "all", "phrase"), default="any")
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
    index = QdrantLocalIndex(settings)
    service = HybridSearchService(
        settings,
        database,
        LexicalSearchService(settings, database),
        VectorSearchService(database, backend, index),
    )
    try:
        response = service.search(
            args.query,
            query_variants=args.variants,
            limit=args.limit,
            candidate_limit=args.candidate_limit,
            lexical_mode=args.lexical_mode,
            article_ids=args.article_ids,
            sections=args.sections,
        )
        if args.json:
            print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))
            return 0

        print("MODE HORS LIGNE ACTIF" if settings.app.offline_mode else "MODE EN LIGNE")
        print(f"Requêtes fusionnées : {response.queries}")
        print(
            f"candidats lexical={response.lexical_candidates} "
            f"vectoriel={response.vector_candidates} uniques={response.unique_candidates} | "
            f"RRF k={response.rrf_k} | durée={response.duration_seconds:.3f}s"
        )
        for result in response.results:
            channels: list[str] = []
            if result.lexical_rank is not None:
                channels.append(f"L{result.lexical_rank}")
            if result.vector_rank is not None:
                channels.append(f"V{result.vector_rank}")
            excerpt = " ".join(result.text.split())
            if len(excerpt) > 240:
                excerpt = f"{excerpt[:237]}..."
            print(
                f"{result.rank}. {result.article_title} — {result.section or 'Section inconnue'}, "
                f"p. {result.page_start}-{result.page_end} — "
                f"RRF={result.hybrid_score:.8f} ({'/'.join(channels)})"
            )
            print(f"   {excerpt}")
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
