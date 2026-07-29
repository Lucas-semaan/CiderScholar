"""Migrate the pre-isolation corpus into the administrator common scope."""

from __future__ import annotations

import json
from dataclasses import asdict

from app.config import load_settings
from app.corpora import CorpusScope, load_local_profile, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.services.corpus_migration import migrate_legacy_corpus
from app.updates.harvest import BibliographicHarvestStore
from app.updates.vector_index import index_bibliographic_abstracts


def main() -> int:
    settings = load_settings()
    report = migrate_legacy_corpus(settings, profile=load_local_profile())
    common_settings = settings_for_corpus(settings, CorpusScope.COMMON)
    store = BibliographicHarvestStore(Database(common_settings.paths.database_path))
    index_report = index_bibliographic_abstracts(
        common_settings,
        store,
        SentenceTransformerBackend(common_settings),
        recreate=True,
    )
    print(
        json.dumps(
            {
                "migration": asdict(report),
                "abstract_index": index_report.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
