"""Import reviewed historical abstracts into the common RAG and rebuild their index."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from app.config import load_settings
from app.corpora import CorpusScope, load_local_profile, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.services.corpus_migration import migrate_legacy_abstracts
from app.updates.harvest import BibliographicHarvestStore
from app.updates.vector_index import index_bibliographic_abstracts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Keep the existing abstract collection and index only pending records",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    migration = migrate_legacy_abstracts(settings, profile=load_local_profile())
    common_settings = settings_for_corpus(settings, CorpusScope.COMMON)
    store = BibliographicHarvestStore(Database(common_settings.paths.database_path))
    index = index_bibliographic_abstracts(
        common_settings,
        store,
        SentenceTransformerBackend(common_settings),
        recreate=not args.incremental,
    )
    print(
        json.dumps(
            {
                "migration": asdict(migration),
                "abstract_index": index.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
