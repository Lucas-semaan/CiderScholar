from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.corpora import CorpusScope, corpus_paths
from app.database.sqlite import Database
from app.deep_research.admission import (
    ClaimAdmissionCheckpoint,
    ClaimAdmissionDecision,
)
from app.deep_research.claims import AtomicClaim, AtomicClaimCheckpoint, AtomicClaimEvidence
from app.deep_research.rendering import SQLiteDeepResearchRenderer
from app.jobs.contracts import DeepResearchPayload


def _payload() -> DeepResearchPayload:
    return DeepResearchPayload(
        message="Rendre les références.",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )


def test_pages_doi_and_bibliography_are_rebuilt_only_from_scoped_sqlite(
    settings,
) -> None:
    database = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
    database.initialize()
    source_text = "La température augmente la concentration en esters."
    database.save_article_and_chunks(
        {
            "id": "sqlite-article",
            "sha256": "d" * 64,
            "doi": "10.1234/sqlite-only",
            "title": "Titre SQLite autoritaire",
            "authors": ["Alice Exemple", "Bob Exemple"],
            "journal": "Journal SQLite",
            "publication_year": 2025,
            "pdf_path": "data/sqlite.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": 7,
                "page_end": 8,
                "chunk_index": 0,
                "text": source_text,
                "token_count": 8,
            }
        ],
    )
    chunk_id = database.article_chunk_ids("sqlite-article")[0]
    claim = AtomicClaim(
        claim_id="claim-" + "e" * 20,
        statement="La température augmente la concentration en esters.",
        role="result",
        evidence=[
            AtomicClaimEvidence(
                scope=CorpusScope.COMMON,
                article_id="sqlite-article",
                chunk_id=chunk_id,
                # Deliberately stale model/checkpoint pages: renderer must ignore these.
                page_start=99,
                page_end=99,
                source_excerpt=source_text,
                source_text_sha256=hashlib.sha256(source_text.encode()).hexdigest(),
            )
        ],
    )
    claims = AtomicClaimCheckpoint(
        question_sha256="f" * 64,
        source_fragment_count=1,
        claims=[claim],
    )
    admission = ClaimAdmissionCheckpoint(
        decisions=[
            ClaimAdmissionDecision(
                claim_id=claim.claim_id,
                status="accepted",
                reason="semantically_supported",
                admitted_statement=claim.statement,
            )
        ]
    )
    renderer = SQLiteDeepResearchRenderer(
        settings,
        settings.paths.cache_dir / "deep_research",
    )
    payload = _payload()

    rendered = renderer.render(payload, claims, admission)

    assert rendered.citations[0].page_start == 7
    assert rendered.citations[0].page_end == 8
    assert "[Corpus commun · sqlite-article, pp. 7–8]" in rendered.answer_markdown
    assert "Titre SQLite autoritaire" in rendered.answer_markdown
    assert "10.1234/sqlite-only" in rendered.answer_markdown
    assert "p. 99" not in rendered.answer_markdown
    assert renderer.load(payload) == rendered


def test_model_generated_doi_is_rejected_before_rendering() -> None:
    with pytest.raises(ValidationError, match="model-generated reference"):
        AtomicClaim(
            claim_id="claim-" + "a" * 20,
            statement="Voir le DOI 10.9999/invente.",
            role="result",
            evidence=[
                AtomicClaimEvidence(
                    scope="common",
                    article_id="article",
                    chunk_id=1,
                    page_start=1,
                    page_end=1,
                    source_excerpt="Un résultat.",
                    source_text_sha256="b" * 64,
                )
            ],
        )
