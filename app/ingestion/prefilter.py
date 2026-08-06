"""Cheap cross-corpus identity checks before full scientific PDF extraction."""

from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.ingestion.deduplication import normalize_title, sha256_file
from app.ingestion.metadata import extract_metadata
from app.ingestion.pdf_extractor import PageText, sorted_page_text

MIN_TITLE_CHARACTERS = 40
MIN_TITLE_WORDS = 5
GENERIC_TITLES = {
    "accepted manuscript",
    "article",
    "document",
    "full text",
    "main document",
    "microsoft word document",
    "publication",
    "research article",
    "untitled",
}


@dataclass(frozen=True, slots=True)
class KnownArticle:
    scope: str
    article_id: str
    sha256: str
    doi: str | None
    title: str
    publication_year: int | None
    pdf_path: str


@dataclass(frozen=True, slots=True)
class ExistingDocumentMatch:
    scope: str
    article_id: str
    reason: Literal["path", "doi", "sha256", "title_candidate"]
    doi: str | None
    title: str


@dataclass(frozen=True, slots=True)
class PdfPreflightResult:
    pdf_path: Path
    sha256: str | None
    doi: str | None
    title: str | None
    publication_year: int | None
    match: ExistingDocumentMatch | None
    title_candidate: ExistingDocumentMatch | None = None


class ExistingCorpusMatcher:
    """Match only durable articles that already have persisted evidence chunks."""

    def __init__(self, articles: list[KnownArticle], *, scan_pages: int = 3) -> None:
        self.scan_pages = scan_pages
        self.by_path = {_path_key(article.pdf_path): article for article in articles}
        self.by_sha256 = {article.sha256: article for article in articles if article.sha256}
        self.by_doi = {article.doi.casefold(): article for article in articles if article.doi}
        self.by_title: dict[str, list[KnownArticle]] = defaultdict(list)
        for article in articles:
            title_key = normalize_title(article.title)
            if _is_specific_title(title_key):
                self.by_title[title_key].append(article)

    @classmethod
    def from_databases(
        cls,
        databases: Mapping[str, Path],
        *,
        scan_pages: int = 3,
    ) -> ExistingCorpusMatcher:
        articles: list[KnownArticle] = []
        seen_paths: set[Path] = set()
        for scope, raw_path in databases.items():
            database_path = raw_path.resolve()
            if database_path in seen_paths or not database_path.is_file():
                continue
            seen_paths.add(database_path)
            uri = f"file:{database_path.as_posix()}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                rows = connection.execute(
                    """
                    SELECT a.id, a.sha256, a.doi, a.title, a.publication_year, a.pdf_path
                    FROM articles AS a
                    WHERE EXISTS (
                        SELECT 1 FROM chunks AS c WHERE c.article_id = a.id
                    )
                    """
                ).fetchall()
            articles.extend(
                KnownArticle(
                    scope=scope,
                    article_id=str(row[0]),
                    sha256=str(row[1]),
                    doi=str(row[2]).casefold() if row[2] else None,
                    title=str(row[3]),
                    publication_year=int(row[4]) if row[4] is not None else None,
                    pdf_path=str(row[5]),
                )
                for row in rows
            )
        return cls(articles, scan_pages=scan_pages)

    def inspect(self, pdf_path: Path) -> PdfPreflightResult:
        path_match = self.by_path.get(_path_key(pdf_path))
        if path_match is not None:
            return _result(pdf_path, match=path_match, reason="path")

        sha256 = sha256_file(pdf_path)
        sha_match = self.by_sha256.get(sha256)
        if sha_match is not None:
            return _result(
                pdf_path,
                sha256=sha256,
                match=sha_match,
                reason="sha256",
            )

        doi, title_candidates, publication_year = self._probe_pdf(pdf_path)
        if doi:
            doi_match = self.by_doi.get(doi.casefold())
            if doi_match is not None:
                return _result(
                    pdf_path,
                    sha256=sha256,
                    doi=doi,
                    title=title_candidates[0] if title_candidates else None,
                    publication_year=publication_year,
                    match=doi_match,
                    reason="doi",
                )

        title_candidate: ExistingDocumentMatch | None = None
        for title in title_candidates:
            title_key = normalize_title(title)
            if not _is_specific_title(title_key):
                continue
            matches = self.by_title.get(title_key, [])
            compatible = [
                article
                for article in matches
                if publication_year is None
                or article.publication_year is None
                or publication_year == article.publication_year
            ]
            if compatible:
                candidate = compatible[0]
                title_candidate = ExistingDocumentMatch(
                    scope=candidate.scope,
                    article_id=candidate.article_id,
                    reason="title_candidate",
                    doi=candidate.doi,
                    title=candidate.title,
                )
                break

        return PdfPreflightResult(
            pdf_path=pdf_path,
            sha256=sha256,
            doi=doi,
            title=title_candidates[0] if title_candidates else None,
            publication_year=publication_year,
            match=None,
            title_candidate=title_candidate,
        )

    def _probe_pdf(self, pdf_path: Path) -> tuple[str | None, list[str], int | None]:
        try:
            import fitz

            with fitz.open(pdf_path) as document:
                metadata = {
                    str(key): str(value)
                    for key, value in (document.metadata or {}).items()
                    if value not in (None, "")
                }
                pages = [
                    PageText(
                        page_number=index + 1,
                        text=sorted_page_text(document.load_page(index)),
                    )
                    for index in range(min(document.page_count, self.scan_pages))
                ]
            extracted = extract_metadata(
                pdf_path=pdf_path,
                document_metadata=metadata,
                pages=pages,
                scan_pages=self.scan_pages,
            )
            candidates = _unique_titles([metadata.get("title", ""), extracted.title, pdf_path.stem])
            return extracted.doi, candidates, extracted.publication_year
        except Exception:
            return None, _unique_titles([pdf_path.stem]), None


def _path_key(path: str | Path) -> str:
    raw_path = str(path)
    if raw_path.startswith("\\\\?\\UNC\\"):
        raw_path = f"\\\\{raw_path[8:]}"
    elif raw_path.startswith("\\\\?\\"):
        raw_path = raw_path[4:]
    return os.path.normcase(os.path.normpath(os.path.abspath(raw_path)))


def _is_specific_title(title_key: str) -> bool:
    return (
        len(title_key) >= MIN_TITLE_CHARACTERS
        and len(title_key.split()) >= MIN_TITLE_WORDS
        and title_key not in GENERIC_TITLES
    )


def _unique_titles(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(value.split()).strip()
        title_key = normalize_title(cleaned)
        if cleaned and title_key not in seen:
            seen.add(title_key)
            output.append(cleaned[:500])
    return output


def _result(
    pdf_path: Path,
    *,
    match: KnownArticle,
    reason: Literal["path", "doi", "sha256"],
    sha256: str | None = None,
    doi: str | None = None,
    title: str | None = None,
    publication_year: int | None = None,
) -> PdfPreflightResult:
    return PdfPreflightResult(
        pdf_path=pdf_path,
        sha256=sha256,
        doi=doi,
        title=title,
        publication_year=publication_year,
        match=ExistingDocumentMatch(
            scope=match.scope,
            article_id=match.article_id,
            reason=reason,
            doi=match.doi,
            title=match.title,
        ),
    )
