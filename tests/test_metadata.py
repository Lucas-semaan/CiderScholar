from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ingestion.metadata import (
    extract_doi,
    extract_metadata,
    is_reliable_local_title,
    is_unidentifiable_local_title,
    propose_local_publication_year,
)
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


@pytest.mark.parametrize(
    "unreliable_title",
    [
        "untitled",
        "Review",
        "Slide 1",
        "DOI: 10.1000/example",
        "8815",
        "I",
        "Microsoft Word - ArticleDOI_Link.docx",
        "LCTC02052013_001.pdf",
    ],
)
def test_local_title_quality_rejects_labels_and_identifiers(unreliable_title: str) -> None:
    assert is_reliable_local_title(unreliable_title) is False


def test_short_named_document_is_reviewed_without_being_treated_as_unidentifiable() -> None:
    assert is_reliable_local_title("Twister®") is False
    assert is_unidentifiable_local_title("Twister®") is False


def test_local_title_quality_rejects_an_introductory_paragraph() -> None:
    paragraph = (
        "This study investigates the influence of storage temperature on cider quality. "
        "The experiments were repeated over several months. Results are discussed below."
    )

    assert is_reliable_local_title(paragraph) is False


def test_metadata_uses_descriptive_filename_after_unreliable_pdf_title(tmp_path: Path) -> None:
    metadata = extract_metadata(
        pdf_path=tmp_path / "Ceviz-2009-Thermal-resistance-in-apple-juice.pdf",
        document_metadata={"title": "untitled"},
        pages=[PageText(1, "Slide 1\nDOI: 10.1000/example")],
    )

    assert metadata.title == "Ceviz 2009 Thermal resistance in apple juice"


def test_metadata_can_identify_title_from_admitted_ocr_text(tmp_path: Path) -> None:
    metadata = extract_metadata(
        pdf_path=tmp_path / ("a" * 12) / "scan.pdf",
        document_metadata={"title": "untitled"},
        pages=[
            PageText(
                1,
                "Stability of apple juice during thermosonication\nAbstract\nText",
                source_kind="windows_ocr",
                ocr_language="fr-FR",
                ocr_confidence=0.91,
            )
        ],
    )

    assert metadata.title == "Stability of apple juice during thermosonication"


def test_metadata_skips_journal_header_before_multiline_article_title(tmp_path: Path) -> None:
    metadata = extract_metadata(
        pdf_path=tmp_path / ("a" * 64 + ".pdf"),
        document_metadata={},
        pages=[
            PageText(
                1,
                "THE JOURNAL OF PHARMACOLOGY\nVol. 316, No. 2\n\n"
                "Nucleoside Ester Prodrug Substrate Specificity of Liver\n"
                "Carboxylesterase\n\nAda Author and Bob Author",
            )
        ],
    )

    assert (
        metadata.title == "Nucleoside Ester Prodrug Substrate Specificity of Liver Carboxylesterase"
    )


def test_metadata_does_not_prefer_a_long_front_matter_paragraph_over_the_title(
    tmp_path: Path,
) -> None:
    metadata = extract_metadata(
        pdf_path=tmp_path / ("a" * 64 + ".pdf"),
        document_metadata={},
        pages=[
            PageText(
                1,
                "Cider Aroma During Bottle Ageing\n\n"
                "This study describes many observations collected during storage and "
                "explains how the analytical measurements were compared across batches "
                "under controlled conditions for a complete statistical assessment.\n\n"
                "Abstract\nText",
            )
        ],
    )

    assert metadata.title == "Cider Aroma During Bottle Ageing"


def test_metadata_uses_exact_local_file_fallback_when_no_title_is_identifiable(
    tmp_path: Path,
) -> None:
    metadata = extract_metadata(
        pdf_path=tmp_path / "e731d7a29b28a32025472f68d70a3e580812a4c7c098fbf35d1fc9bb9f8c763f.pdf",
        document_metadata={"title": "Review"},
        pages=[PageText(1, "Slide 1\nDOI: 10.1000/example")],
    )

    assert metadata.title == "fichier local"


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


def test_local_year_audit_requires_unambiguous_front_matter_evidence() -> None:
    assert propose_local_publication_year(
        [PageText(1, "Published online 2014\nReferences: 1998, 2002")],
        latest_year=2026,
    ) == (2014, "pdf_explicit_publication_year")
    assert (
        propose_local_publication_year([PageText(1, "References: 1998, 2002")], latest_year=2026)
        is None
    )


def test_article_model_rejects_malformed_doi(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="invalid DOI"):
        ArticleMetadata(
            title="Synthetic",
            doi="made-up-doi",
            pdf_path=tmp_path / "article.pdf",
        )
