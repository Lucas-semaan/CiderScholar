from __future__ import annotations

import hashlib
from uuid import uuid4

from app.corpora import CorpusScope, corpus_paths
from app.database.sqlite import Database
from app.deep_research.citations import (
    CitationSourceFragment,
    CitationTraversalStage,
    SQLiteCitationTargetResolver,
    extract_dois,
)
from app.jobs.contracts import DeepResearchPayload


def _article(article_id: str, doi: str) -> dict[str, object]:
    return {
        "id": article_id,
        "sha256": hashlib.sha256(article_id.encode()).hexdigest(),
        "doi": doi,
        "title": f"Article {article_id}",
        "authors": [],
        "pdf_path": f"data/{article_id}.pdf",
        "validation_status": "validated",
        "source": "local",
    }


def _chunk(text: str) -> dict[str, object]:
    return {
        "section": "References",
        "page_start": 3,
        "page_end": 3,
        "chunk_index": 0,
        "text": text,
        "token_count": 8,
    }


def _payload() -> DeepResearchPayload:
    return DeepResearchPayload(
        message="Suivre les références disponibles.",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )


def test_extract_dois_is_normalized_and_unique() -> None:
    text = "Voir https://doi.org/10.1234/ABC.1, puis 10.56789/test(2). Répétition: 10.1234/abc.1."

    assert extract_dois(text) == ("10.1234/abc.1", "10.56789/test(2")


def test_traversal_persists_relation_reason_and_truthful_local_access(settings) -> None:
    common = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
    common.initialize()
    common.save_article_and_chunks(
        _article("source", "10.1000/source"),
        [_chunk("Références 10.1000/local et 10.1000/missing.")],
    )
    common.save_article_and_chunks(
        _article("target", "10.1000/local"),
        [_chunk("Résultat cible réellement consultable.")],
    )
    source_chunk_id = common.article_chunk_ids("source")[0]
    stage = CitationTraversalStage(
        SQLiteCitationTargetResolver(settings),
        settings.paths.cache_dir / "deep_research",
    )
    payload = _payload()

    checkpoint = stage.traverse(
        payload,
        [
            CitationSourceFragment(
                scope=CorpusScope.COMMON,
                article_id="source",
                chunk_id=source_chunk_id,
                page_start=3,
                page_end=3,
                text="Références 10.1000/local et 10.1000/missing.",
            )
        ],
    )

    assert len(checkpoint.entries) == 2
    local, missing = checkpoint.entries
    assert local.relation == "references"
    assert local.addition_reason == "doi_explicitly_observed_in_consulted_fragment"
    assert local.target_doi == "10.1000/local"
    assert local.target_scope is CorpusScope.COMMON
    assert local.target_article_id == "target"
    assert local.access_status == "consulted_local_text"
    assert (
        local.consulted_text_sha256
        == hashlib.sha256("Résultat cible réellement consultable.".encode()).hexdigest()
    )
    assert missing.target_doi == "10.1000/missing"
    assert missing.access_status == "unavailable"
    assert missing.consulted_chunk_id is None
    persisted = stage._path(payload).read_text(encoding="utf-8")
    assert "Résultat cible réellement consultable" not in persisted


def test_traversal_is_bounded_and_restart_idempotent(settings) -> None:
    for scope in (CorpusScope.COMMON,):
        Database(corpus_paths(settings, scope).database_path).initialize()
    stage = CitationTraversalStage(
        SQLiteCitationTargetResolver(settings),
        settings.paths.cache_dir / "deep_research",
    )
    payload = _payload()
    text = " ".join(f"10.2000/reference-{index}" for index in range(12))
    fragment = CitationSourceFragment(
        scope=CorpusScope.COMMON,
        article_id="source",
        chunk_id=1,
        page_start=1,
        page_end=1,
        text=text,
    )

    first = stage.traverse(payload, [fragment])
    second = stage.traverse(
        payload,
        [fragment.model_copy(update={"text": "10.9999/new-reference"})],
    )

    assert len(first.entries) == 8
    assert second == first
    assert all(entry.depth == 1 for entry in first.entries)
