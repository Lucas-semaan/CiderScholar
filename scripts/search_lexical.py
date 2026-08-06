"""Run a safe local SQLite FTS5 search from the command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.retrieval.lexical_search import LexicalSearchService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Natural-language or scientific lexical query")
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--limit", type=int, help="Maximum number of chunks")
    parser.add_argument("--mode", choices=("any", "all", "phrase"), default="any")
    parser.add_argument("--article-id", action="append", dest="article_ids")
    parser.add_argument("--section", action="append", dest="sections")
    parser.add_argument("--json", action="store_true", help="Print strict JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = settings_for_corpus(load_settings(args.config), CorpusScope.COMMON)
    database = Database(settings.paths.database_path)
    database.initialize()
    response = LexicalSearchService(settings, database).search(
        args.query,
        limit=args.limit,
        mode=args.mode,
        article_ids=args.article_ids,
        sections=args.sections,
    )

    if args.json:
        print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    print("MODE HORS LIGNE ACTIF" if settings.app.offline_mode else "MODE EN LIGNE")
    print(f"Termes : {', '.join(response.query.terms) or '(aucun)'}")
    print(
        f"Expression FTS5 : {response.query.fts5_expression or '(vide)'} | "
        f"résultats={len(response.results)} | durée={response.duration_seconds:.4f}s"
    )
    for result in response.results:
        excerpt = " ".join(result.text.split())
        if len(excerpt) > 240:
            excerpt = f"{excerpt[:237]}..."
        print(
            f"{result.rank}. {result.article_title} — {result.section or 'Section inconnue'}, "
            f"p. {result.page_start}-{result.page_end} — BM25={result.relevance_score:.6f}"
        )
        print(f"   {excerpt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
