from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ingestion.metadata import extract_doi, extract_metadata
from app.ingestion.pdf_extractor import PageText
from app.models.article import ArticleMetadata


def test_doi_is_only_returned_when_present() -> None:
    assert extract_doi(["No persistent identifier in this text."]) is None
    assert extract_doi(["See https://doi.org/10.1234/TEST.567."]) == "10.1234/test.567"


def test_metadata_does_not_invent_missing_doi(tmp_path: Path) -> None:
    metadata = extract_metadata(
        pdf_path=tmp_path / "article.pdf",
        document_metadata={"title": "A synthetic article"},
        pages=[PageText(1, "Abstract\nThis study has no DOI.\nIntroduction\nText")],
    )
    assert metadata.doi is None


def test_metadata_replaces_generic_front_matter_title_from_the_pdf_text(tmp_path: Path) -> None:
    metadata = extract_metadata(
        pdf_path=tmp_path / "handbook.pdf",
        document_metadata={"title": "Front Matter"},
        pages=[
            PageText(
                1,
                "Handbook of Enology Volume 1 The Microbiology of Wine and Vinifications\n"
                "Second edition",
            )
        ],
    )

    assert metadata.title == (
        "Handbook of Enology Volume 1 The Microbiology of Wine and Vinifications"
    )


def test_metadata_uses_custom_pdf_doi_and_deduplicates_authors(tmp_path: Path) -> None:
    metadata = extract_metadata(
        pdf_path=tmp_path / "book.pdf",
        document_metadata={
            "title": "A scientific handbook",
            "author": "Ada Author; Bob Editor; ada author",
            "WPS-ARTICLEDOI": "10.1002/BOOK.123",
        },
        pages=[PageText(1, "A scientific handbook")],
    )

    assert metadata.doi == "10.1002/book.123"
    assert metadata.authors == ["Ada Author", "Bob Editor"]


def test_metadata_ignores_future_numbers_when_extracting_publication_year(
    tmp_path: Path,
) -> None:
    future_year = datetime.now(UTC).year + 1
    metadata = extract_metadata(
        pdf_path=tmp_path / f"newsletter-objective-{future_year}.pdf",
        document_metadata={"title": "A scientific publication", "creationDate": "D:20990101"},
        pages=[
            PageText(
                1,
                f"Objective {future_year}\nJ. Dairy Sci. 84:2125-2135, 2001",
            )
        ],
    )

    assert metadata.publication_year == 2001


def test_article_model_rejects_malformed_doi(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="invalid DOI"):
        ArticleMetadata(
            title="Synthetic",
            doi="made-up-doi",
            pdf_path=tmp_path / "article.pdf",
        )
