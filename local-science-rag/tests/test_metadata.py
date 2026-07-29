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


def test_article_model_rejects_malformed_doi(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="invalid DOI"):
        ArticleMetadata(
            title="Synthetic",
            doi="made-up-doi",
            pdf_path=tmp_path / "article.pdf",
        )
