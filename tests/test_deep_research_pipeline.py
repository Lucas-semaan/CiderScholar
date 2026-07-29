from __future__ import annotations

import hashlib

import pytest

from app.corpora import CorpusScope, corpus_paths
from app.database.sqlite import Database
from app.deep_research.pipeline import SQLiteFragmentTextLoader
from app.deep_research.retrieval import DeepResearchFragmentHit


def _seed_scoped_chunk(
    settings,
    scope: CorpusScope,
    *,
    article_id: str,
    text: str,
) -> DeepResearchFragmentHit:
    database = Database(corpus_paths(settings, scope).database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": article_id,
            "sha256": hashlib.sha256(article_id.encode()).hexdigest(),
            "doi": None,
            "title": f"Article {scope.value}",
            "authors": [],
            "pdf_path": f"data/{scope.value}/article.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": 2,
                "page_end": 3,
                "chunk_index": 0,
                "text": text,
                "token_count": 4,
            }
        ],
    )
    chunk_id = database.article_chunk_ids(article_id)[0]
    return DeepResearchFragmentHit(
        method="rrf",
        scope=scope,
        article_id=article_id,
        chunk_id=chunk_id,
        page_start=2,
        page_end=3,
        score=0.1,
        rrf_score=0.1,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def test_sqlite_fragment_loader_rehydrates_each_scope_without_identity_mix(settings) -> None:
    common = _seed_scoped_chunk(
        settings,
        CorpusScope.COMMON,
        article_id="common-article",
        text="common authoritative text",
    )
    private = _seed_scoped_chunk(
        settings,
        CorpusScope.PRIVATE,
        article_id="private-article",
        text="private authoritative text",
    )

    loaded = SQLiteFragmentTextLoader(settings).load([common, private])

    assert loaded[(CorpusScope.COMMON, "common-article", common.chunk_id)] == (
        "common authoritative text"
    )
    assert loaded[(CorpusScope.PRIVATE, "private-article", private.chunk_id)] == (
        "private authoritative text"
    )

    mixed_identity = private.model_copy(update={"article_id": "common-article"})
    with pytest.raises(RuntimeError, match="provenance changed"):
        SQLiteFragmentTextLoader(settings).load([mixed_identity])
