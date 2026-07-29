"""Generate explicitly non-citable table/figure captions for local retrieval."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.config import load_settings
from app.corpora import CorpusScope, corpus_paths, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.visual_enrichment import SyntheticCaptionEnricher
from app.llm.argo_client import ArgoClient


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--scope", choices=["common", "private"], required=True)
    parser.add_argument("--article-id", required=True)
    parser.add_argument("--allow-argo", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    if not arguments.allow_argo:
        raise SystemExit("--allow-argo is required because this command calls ARGO")
    settings = load_settings(arguments.config)
    scope = CorpusScope(arguments.scope)
    scoped = settings_for_corpus(settings, scope)
    database = Database(corpus_paths(settings, scope).database_path)
    client = ArgoClient(scoped)
    try:
        count = SyntheticCaptionEnricher(database, client).enrich_article(arguments.article_id)
    finally:
        client.close()
    print(f"article_id={arguments.article_id}")
    print(f"scope={scope.value}")
    print(f"synthetic_captions={count}")
    print("citable_synthetic_captions=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
