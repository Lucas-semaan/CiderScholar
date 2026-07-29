from __future__ import annotations

from io import BytesIO

import pytest
from pydantic import ValidationError

from app.corpora import CorpusScope
from app.models.synthesis import BibliographyEntry
from app.services.workflows import (
    apply_runtime_overrides,
    bibliography_to_bibtex,
    pdf_paths,
    save_uploaded_pdf,
)


def test_uploaded_pdf_is_sanitized_hashed_and_confined(settings) -> None:
    destination = save_uploaded_pdf(
        settings,
        original_name="../étude locale.pdf",
        stream=BytesIO(b"%PDF-1.7\nsynthetic"),
    )

    assert destination.read_bytes() == b"%PDF-1.7\nsynthetic"
    assert destination.suffix == ".pdf"
    assert destination.parent == settings.paths.pdf_dir / "uploads"
    assert ".." not in destination.name

    with pytest.raises(ValueError, match=".pdf"):
        save_uploaded_pdf(settings, original_name="notes.txt", stream=BytesIO(b"not a pdf"))


def test_pdf_folder_discovery_is_explicit_and_recursive(settings) -> None:
    root = settings.paths.pdf_dir
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "one.pdf").write_bytes(b"one")
    (nested / "two.pdf").write_bytes(b"two")
    (nested / "ignored.txt").write_text("ignored", encoding="utf-8")

    assert [path.name for path in pdf_paths(root, recursive=False)] == ["one.pdf"]
    assert [path.name for path in pdf_paths(root, recursive=True)] == [
        "two.pdf",
        "one.pdf",
    ]


def test_runtime_overrides_are_validated_without_mutating_base(settings) -> None:
    updated = apply_runtime_overrides(
        settings,
        {
            "argo": {"model": "alternate-argo-model"},
            "retrieval": {
                "lexical_weight": 0.4,
                "vector_weight": 0.4,
                "reranker_weight": 0.2,
            },
        },
    )

    assert updated.argo.model == "alternate-argo-model"
    assert settings.argo.model == "chat-gpt-oss-120b"
    with pytest.raises(ValidationError, match="weights must add up"):
        apply_runtime_overrides(
            settings,
            {"retrieval": {"lexical_weight": 0.9}},
        )


def test_bibtex_uses_only_structured_bibliography_metadata() -> None:
    rendered = bibliography_to_bibtex(
        [
            BibliographyEntry(
                article_id="article/1",
                title="Local {study}",
                authors=["Ada Test", "Jean Exemple"],
                journal="SQLite Journal",
                publication_year=2026,
                doi="10.1000/from-sqlite",
            )
        ]
    )

    assert rendered.startswith("@article{article-1,")
    assert "Ada Test and Jean Exemple" in rendered
    assert "doi = {10.1000/from-sqlite}" in rendered
    assert "Local \\{study\\}" in rendered
    assert "ciderscholar_scope = {common}" in rendered
    assert "note = {Corpus commun}" in rendered


def test_bibtex_never_exports_a_private_source_as_common() -> None:
    rendered = bibliography_to_bibtex(
        [
            BibliographyEntry(
                article_id="private-1",
                scope=CorpusScope.PRIVATE,
                title="Private study",
                authors=[],
                journal=None,
                publication_year=None,
                doi=None,
            )
        ]
    )

    assert "ciderscholar_scope = {private}" in rendered
    assert "note = {Document privé}" in rendered
    assert "Corpus commun" not in rendered
