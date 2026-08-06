from __future__ import annotations

from pathlib import Path

import fitz

from app.ingestion.deduplication import sha256_file
from app.ingestion.prefilter import ExistingCorpusMatcher, KnownArticle


def _known(
    *,
    sha256: str = "a" * 64,
    doi: str | None = None,
    title: str = "Fermentation kinetics and volatile compounds in cider production",
    year: int | None = 2024,
    pdf_path: str = "C:/known/article.pdf",
) -> KnownArticle:
    return KnownArticle(
        scope="common",
        article_id="known-article",
        sha256=sha256,
        doi=doi,
        title=title,
        publication_year=year,
        pdf_path=pdf_path,
    )


def _pdf(path: Path, text: str) -> Path:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()
    return path


def test_prefilter_skips_known_path_before_hashing(tmp_path: Path) -> None:
    pdf = tmp_path / "known.pdf"
    pdf.write_bytes(b"not opened because the path is already durable")
    matcher = ExistingCorpusMatcher([_known(pdf_path=str(pdf))])

    result = matcher.inspect(pdf)

    assert result.match is not None
    assert result.match.reason == "path"
    assert result.sha256 is None


def test_prefilter_skips_exact_sha_before_opening_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "same-bytes.pdf"
    pdf.write_bytes(b"exact durable bytes")
    matcher = ExistingCorpusMatcher([_known(sha256=sha256_file(pdf))])

    result = matcher.inspect(pdf)

    assert result.match is not None
    assert result.match.reason == "sha256"


def test_prefilter_skips_doi_found_in_first_pages(tmp_path: Path) -> None:
    pdf = _pdf(
        tmp_path / "same-doi.pdf",
        "Different rendering of the article\nDOI: 10.1234/SHARED.TEST\nResults",
    )
    matcher = ExistingCorpusMatcher([_known(doi="10.1234/shared.test")])

    result = matcher.inspect(pdf)

    assert result.match is not None
    assert result.match.reason == "doi"
    assert result.sha256 == sha256_file(pdf)


def test_prefilter_never_skips_on_title_alone(tmp_path: Path) -> None:
    title = "Fermentation kinetics and volatile compounds in cider production"
    pdf = _pdf(tmp_path / "same-title.pdf", f"{title}\nIndependent results without DOI")
    matcher = ExistingCorpusMatcher([_known(title=title, year=None)])

    result = matcher.inspect(pdf)

    assert result.match is None
    assert result.title_candidate is not None
    assert result.title_candidate.reason == "title_candidate"
    assert result.sha256 == sha256_file(pdf)
