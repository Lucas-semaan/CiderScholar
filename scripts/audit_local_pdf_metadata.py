"""Preview safer titles for locally indexed PDFs without changing SQLite."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.config import load_settings
from app.ingestion.metadata import (
    extract_metadata,
    is_reliable_local_title,
    is_unidentifiable_local_title,
    propose_local_publication_year,
)
from app.ingestion.pdf_extractor import ExtractedDocument, PdfExtractor, PyMuPdfExtractor
from app.ingestion.windows_ocr import WindowsOcrPdfExtractor

_TERRA_WORK_TYPES = {
    "journal_article": "article",
    "conference_paper": "conference-paper",
    "book": "book",
    "book_chapter": "book-chapter",
    "thesis": "dissertation",
    "report": "report",
    "presentation": "presentation",
    "regulatory": "regulatory",
    "technical_sheet": "technical-sheet",
    "bulletin": "bulletin",
    "patent": "patent",
    "other": "other",
}


def _local_rows(connection: sqlite3.Connection, limit: int | None) -> Iterable[sqlite3.Row]:
    query = """
        SELECT id, sha256, doi, title, publication_year, pdf_path, source
        FROM articles
        WHERE lower(coalesce(source, '')) = 'local'
        ORDER BY created_at, id
    """.strip()
    # The caller filters title quality in Python so that the current and proposed
    # policies remain inspectable in one place.
    yield from (
        connection.execute(query)
        if limit is None
        else connection.execute(f"{query} LIMIT ?", (limit,))
    )


def _cached_document(cache_path: Path, expected_sha256: str) -> ExtractedDocument | None:
    try:
        value = json.loads(cache_path.read_text(encoding="utf-8"))
        document = ExtractedDocument.from_dict(value)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError):
        return None
    expected_name = f"{expected_sha256}.pages.json".casefold()
    return document if cache_path.name.casefold() == expected_name else None


def _article_fingerprint(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for row in connection.execute(
        "SELECT id, sha256, doi, title, publication_year FROM articles ORDER BY id"
    ):
        digest.update("\x1f".join(str(value or "") for value in row).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _validated_bibliographic_metadata(
    connection: sqlite3.Connection,
) -> dict[str, tuple[str, int | None]]:
    has_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'bibliographic_records'"
    ).fetchone()
    if has_table is None:
        return {}
    return {
        str(row["doi"]).casefold(): (
            str(row["title"]),
            int(row["publication_year"]) if row["publication_year"] is not None else None,
        )
        for row in connection.execute(
            """
            SELECT doi, title, publication_year
            FROM bibliographic_records
            WHERE relevance_status = 'accepted'
              AND doi IS NOT NULL AND trim(doi) != ''
              AND (publication_year IS NULL OR publication_year BETWEEN 1600 AND ?)
            """,
            (datetime.now(UTC).year,),
        )
    }


def _save_audit_cache(cache_path: Path, document: ExtractedDocument) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document.to_dict(), ensure_ascii=False), encoding="utf-8")
    temporary.replace(cache_path)


def _candidate(
    row: sqlite3.Row,
    document: ExtractedDocument | None,
    *,
    bibliographic_metadata: dict[str, tuple[str, int | None]],
    pdf_unavailable: bool,
) -> dict[str, object] | None:
    current_title = str(row["title"] or "")
    current_year = row["publication_year"]
    proposed_title = current_title
    title_reason = "title_already_reliable"
    title_provenance = "articles.title"
    proposed_year: int | None = None
    year_reason = "no_verified_year_proposal"
    year_provenance = None
    confidence = "review"
    doi = str(row["doi"] or "").casefold()
    validated = bibliographic_metadata.get(doi)
    if not is_reliable_local_title(current_title):
        if validated is not None and is_reliable_local_title(validated[0]):
            proposed_title = validated[0]
            title_reason = "exact_doi_validated_bibliographic_record"
            title_provenance = "bibliographic_records.accepted"
            confidence = "high"
        elif document is None:
            proposed_title = "fichier local"
            title_reason = "pdf_unavailable"
            title_provenance = "none"
        else:
            metadata = extract_metadata(
                pdf_path=Path(str(row["pdf_path"])),
                document_metadata=document.metadata,
                pages=document.pages,
            )
            proposed_title = metadata.title
            title_reason = "metadata_reextracted"
            title_provenance = (
                "windows_ocr"
                if any(page.source_kind == "windows_ocr" for page in document.pages[:3])
                else "pdf_native_or_filename"
            )
    if validated is not None and validated[1] is not None:
        proposed_year = validated[1]
        year_reason = "exact_doi_validated_bibliographic_record"
        year_provenance = "bibliographic_records.accepted"
        confidence = "high"
    elif document is not None:
        local_year = propose_local_publication_year(document.pages)
        if local_year is not None:
            proposed_year, year_reason = local_year
            year_provenance = (
                "windows_ocr"
                if any(page.source_kind == "windows_ocr" for page in document.pages[:3])
                else "pdf_native"
            )
    changes_title = proposed_title != current_title
    changes_year = proposed_year is not None and proposed_year != current_year
    if not changes_title and not changes_year:
        return None
    return {
        "record_id": str(row["id"]),
        "sha256": str(row["sha256"]),
        "current_title": current_title,
        "proposed_title": proposed_title,
        "title_reason": title_reason,
        "title_provenance": title_provenance,
        "current_year": current_year,
        "proposed_year": proposed_year,
        "year_reason": year_reason,
        "year_provenance": year_provenance,
        "confidence": confidence,
        "accepted": False,
        "clear_year": False,
        "pdf_unavailable": pdf_unavailable,
    }


def audit_local_pdf_metadata(
    *,
    database_path: Path,
    extracted_dir: Path,
    limit: int | None,
    ocr_extractor: PdfExtractor | None = None,
    audit_cache_dir: Path | None = None,
) -> dict[str, object]:
    """Return a read-only preview of local-title repairs and unavailable PDFs."""

    uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    extractor = PyMuPdfExtractor()
    bibliographic_metadata = _validated_bibliographic_metadata(connection)
    candidates: list[dict[str, object]] = []
    unavailable = 0
    inspected = 0
    scanned = 0
    database_fingerprint = _article_fingerprint(connection)
    try:
        for row in _local_rows(connection, limit):
            scanned += 1
            current_title = str(row["title"] or "")
            current_year = row["publication_year"]
            validated = bibliographic_metadata.get(str(row["doi"] or "").casefold())
            exact_metadata_differs = validated is not None and (
                (
                    not is_reliable_local_title(current_title)
                    and is_reliable_local_title(validated[0])
                    and validated[0] != current_title
                )
                or (validated[1] is not None and validated[1] != current_year)
            )
            if (
                is_reliable_local_title(current_title)
                and current_year is not None
                and not exact_metadata_differs
            ):
                continue
            inspected += 1
            pdf_path = Path(str(row["pdf_path"])).resolve()
            if not pdf_path.is_file():
                unavailable += 1
                candidate = _candidate(
                    row,
                    None,
                    bibliographic_metadata=bibliographic_metadata,
                    pdf_unavailable=True,
                )
                if candidate is not None:
                    candidates.append(candidate)
                continue
            audit_cache_path = (
                audit_cache_dir / f"{row['sha256']}.pages.json"
                if audit_cache_dir is not None
                else None
            )
            document = (
                _cached_document(audit_cache_path, str(row["sha256"]))
                if audit_cache_path is not None
                else None
            )
            if document is None:
                document = _cached_document(
                    extracted_dir / f"{row['sha256']}.pages.json", str(row["sha256"])
                )
            if document is None:
                document = extractor.extract(pdf_path)
            first_page_is_poor = not document.pages or len(
                document.pages[0].text.strip()
            ) < getattr(ocr_extractor, "min_page_text_characters", 25)
            if ocr_extractor is not None and (
                document.requires_ocr
                or (not is_reliable_local_title(str(row["title"] or "")) and first_page_is_poor)
            ):
                document = ocr_extractor.extract(pdf_path)
                if audit_cache_path is not None:
                    _save_audit_cache(audit_cache_path, document)
            candidate = _candidate(
                row,
                document,
                bibliographic_metadata=bibliographic_metadata,
                pdf_unavailable=False,
            )
            if candidate is not None:
                candidates.append(candidate)
    finally:
        connection.close()
    return {
        "mode": "preview_only",
        "database_fingerprint": database_fingerprint,
        "scanned_local_records": scanned,
        "inspected_local_records": inspected,
        "pdf_unavailable": unavailable,
        "ocr_enabled": ocr_extractor is not None,
        "candidates": candidates,
    }


def _snapshot_database(database_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = (
        backup_dir / f"before-local-metadata-apply-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.sqlite3"
    )
    temporary = destination.with_suffix(".tmp")
    try:
        with (
            closing(sqlite3.connect(database_path)) as source,
            closing(sqlite3.connect(temporary)) as target,
        ):
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("SQLite backup integrity check failed")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def apply_reviewed_audit(
    *, database_path: Path, audit: dict[str, object], backup_dir: Path
) -> dict[str, object]:
    """Apply reviewed, exact-DOI, or explicit local-fallback corrections atomically."""

    expected_fingerprint = audit.get("database_fingerprint")
    candidates = audit.get("candidates")
    if not isinstance(expected_fingerprint, str) or not isinstance(candidates, list):
        raise ValueError("audit must contain database_fingerprint and candidates")
    with closing(
        sqlite3.connect(f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True)
    ) as read_only:
        if _article_fingerprint(read_only) != expected_fingerprint:
            raise RuntimeError("articles changed since the audit; generate and review a new audit")
    backup = _snapshot_database(database_path, backup_dir)
    applied: list[dict[str, object]] = []
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        try:
            if _article_fingerprint(connection) != expected_fingerprint:
                raise RuntimeError("articles changed before the metadata transaction")
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    raise ValueError("audit candidate must be an object")
                record_id = candidate.get("record_id")
                if not isinstance(record_id, str):
                    raise ValueError("audit candidate is missing record_id")
                current = connection.execute(
                    "SELECT sha256, title, publication_year FROM articles WHERE id = ?",
                    (record_id,),
                ).fetchone()
                if current is None:
                    raise RuntimeError(f"audit target disappeared: {record_id}")
                if (
                    str(current["sha256"]) != str(candidate.get("sha256"))
                    or str(current["title"] or "") != str(candidate.get("current_title") or "")
                    or current["publication_year"] != candidate.get("current_year")
                ):
                    raise RuntimeError(f"audit target changed: {record_id}")
                accepted = candidate.get("accepted") is True
                exact_doi_year = (
                    candidate.get("confidence") == "high"
                    and candidate.get("year_reason") == "exact_doi_validated_bibliographic_record"
                )
                exact_doi_title = (
                    candidate.get("confidence") == "high"
                    and candidate.get("title_reason") == "exact_doi_validated_bibliographic_record"
                )
                explicit_local_fallback = (
                    candidate.get("proposed_title") == "fichier local"
                    and candidate.get("title_reason") in {"metadata_reextracted", "pdf_unavailable"}
                    and is_unidentifiable_local_title(str(candidate.get("current_title") or ""))
                )
                assignments: list[str] = []
                parameters: list[object] = []
                if (accepted or exact_doi_title or explicit_local_fallback) and isinstance(
                    candidate.get("proposed_title"), str
                ):
                    title = str(candidate["proposed_title"])
                    if title != current["title"]:
                        assignments.append("title = ?")
                        parameters.append(title)
                proposed_year = candidate.get("proposed_year")
                if (accepted or exact_doi_year) and isinstance(proposed_year, int):
                    if proposed_year < 1600 or proposed_year > datetime.now(UTC).year:
                        raise ValueError(f"invalid proposed year for {record_id}")
                    if proposed_year != current["publication_year"]:
                        assignments.append("publication_year = ?")
                        parameters.append(proposed_year)
                elif accepted and candidate.get("clear_year") is True:
                    if current["publication_year"] is not None:
                        assignments.append("publication_year = ?")
                        parameters.append(None)
                if assignments:
                    parameters.append(record_id)
                    connection.execute(
                        f"UPDATE articles SET {', '.join(assignments)} WHERE id = ?", parameters
                    )
                    applied.append(
                        {
                            "record_id": record_id,
                            "fields": [value.split()[0] for value in assignments],
                        }
                    )
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("SQLite integrity check failed after metadata transaction")
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
    return {"mode": "applied", "backup": str(backup), "applied": applied}


def _review_authors(value: object, *, record_id: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"invalid proposed authors for {record_id}")
    authors: list[str] = []
    seen: set[str] = set()
    for item in value:
        author = " ".join(item.split())
        if not author or len(author) > 300 or any(ord(character) < 32 for character in author):
            raise ValueError(f"invalid proposed author for {record_id}")
        key = author.casefold()
        if key not in seen:
            seen.add(key)
            authors.append(author)
    if len(authors) > 100:
        raise ValueError(f"too many proposed authors for {record_id}")
    return authors


def _review_has_evidence(value: object) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for evidence in value:
        if not isinstance(evidence, dict):
            return False
        if evidence.get("source") not in {
            "native_metadata",
            "native_text",
            "ocr",
            "filename",
        }:
            return False
        note = evidence.get("note")
        if not isinstance(note, str) or len(note.strip()) < 3:
            return False
        page = evidence.get("page")
        if page is not None and (not isinstance(page, int) or page < 1):
            return False
    return True


def _load_terra_reviews(review_paths: list[Path]) -> list[dict[str, object]]:
    reviews: list[dict[str, object]] = []
    record_ids: set[str] = set()
    for path in review_paths:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    review = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"invalid JSON in {path}:{line_number}") from error
                if not isinstance(review, dict):
                    raise ValueError(f"review must be an object in {path}:{line_number}")
                record_id = review.get("record_id")
                if not isinstance(record_id, str) or not record_id:
                    raise ValueError(f"review is missing record_id in {path}:{line_number}")
                if record_id in record_ids:
                    raise ValueError(f"duplicate Terra review for {record_id}")
                record_ids.add(record_id)
                reviews.append(review)
    return reviews


def apply_terra_reviews(
    *,
    database_path: Path,
    review_paths: list[Path],
    backup_dir: Path,
    title_only: bool = False,
) -> dict[str, object]:
    """Apply evidence-backed Terra reviews to local records after a verified backup."""

    reviews = _load_terra_reviews(review_paths)
    if title_only:
        for review in reviews:
            current = review.get("current")
            proposed = review.get("proposed")
            if isinstance(current, dict) and isinstance(proposed, dict):
                proposed["authors"] = current.get("authors")
                proposed["year"] = None
                proposed["work_type"] = None
    accepted = [
        review
        for review in reviews
        if review.get("accepted") is True and review.get("confidence") in {"high", "medium"}
    ]
    prepared: list[dict[str, object]] = []
    uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        for review in accepted:
            record_id = str(review["record_id"])
            if not _review_has_evidence(review.get("evidence")):
                raise ValueError(f"accepted Terra review lacks usable evidence: {record_id}")
            current_review = review.get("current")
            proposed = review.get("proposed")
            if not isinstance(current_review, dict) or not isinstance(proposed, dict):
                raise ValueError(f"invalid Terra review shape for {record_id}")
            current = connection.execute(
                """
                SELECT sha256, title, authors, publication_year, work_type, source
                FROM articles WHERE id = ?
                """,
                (record_id,),
            ).fetchone()
            if current is None or str(current["source"] or "").casefold() != "local":
                raise RuntimeError(f"Terra review target is not a local record: {record_id}")
            if str(current["sha256"]) != str(review.get("sha256")):
                raise RuntimeError(f"Terra review SHA changed: {record_id}")
            current_authors = json.loads(str(current["authors"] or "[]"))
            if (
                str(current["title"]) != str(current_review.get("title"))
                or current["publication_year"] != current_review.get("year")
                or current["work_type"] != current_review.get("work_type")
            ):
                raise RuntimeError(f"Terra review target changed: {record_id}")

            assignments: dict[str, object] = {}
            title = proposed.get("title")
            if not isinstance(title, str):
                raise ValueError(f"invalid proposed title for {record_id}")
            if title != current["title"]:
                verified_title_only = (
                    title_only
                    and review.get("confidence") == "high"
                    and not is_unidentifiable_local_title(title)
                    and len(title) <= 500
                    and sum(character.isalpha() for character in title) >= 4
                )
                if (
                    title != "fichier local"
                    and not is_reliable_local_title(title)
                    and not verified_title_only
                ):
                    raise ValueError(f"invalid proposed title for {record_id}")
                assignments["title"] = title
            authors = _review_authors(proposed.get("authors"), record_id=record_id)
            current_authors_are_corrupted = any(
                "\ufffd" in author for author in current_authors if isinstance(author, str)
            )
            clears_unidentifiable_fallback = (
                not authors
                and title == "fichier local"
                and is_unidentifiable_local_title(str(current["title"]))
            )
            explicitly_replaces_authors = (
                review.get("replace_authors") is True
                and review.get("confidence") == "high"
                and bool(authors)
            )
            if authors != current_authors and (
                not current_authors
                or (authors and current_authors_are_corrupted)
                or clears_unidentifiable_fallback
                or explicitly_replaces_authors
            ):
                assignments["authors"] = json.dumps(authors, ensure_ascii=False)
            year = proposed.get("year")
            if year is not None:
                if not isinstance(year, int) or not 1600 <= year <= datetime.now(UTC).year:
                    raise ValueError(f"invalid proposed year for {record_id}")
                if year != current["publication_year"]:
                    assignments["publication_year"] = year
            work_type = proposed.get("work_type")
            if work_type is not None:
                if not isinstance(work_type, str) or work_type not in _TERRA_WORK_TYPES:
                    raise ValueError(f"invalid proposed work type for {record_id}")
                canonical_work_type = _TERRA_WORK_TYPES[work_type]
                title_key = title.casefold()
                supported_thesis = work_type != "thesis" or (
                    (
                        any(token in title_key for token in ("thesis", "thèse", "dissertation"))
                        and "template" not in title_key
                    )
                    or (
                        review.get("verified_document_type") is True
                        and review.get("confidence") == "high"
                    )
                )
                if (
                    canonical_work_type != current["work_type"]
                    and (review.get("confidence") == "high" or canonical_work_type != "other")
                    and supported_thesis
                ):
                    assignments["work_type"] = canonical_work_type
            if assignments:
                prepared.append(
                    {
                        "record_id": record_id,
                        "sha256": str(current["sha256"]),
                        "assignments": assignments,
                    }
                )

    if not prepared:
        return {
            "mode": "no_changes",
            "reviewed": len(reviews),
            "accepted": len(accepted),
            "title_only": title_only,
            "applied": [],
        }
    backup = _snapshot_database(database_path, backup_dir)
    applied: list[dict[str, object]] = []
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for update in prepared:
                assignments = update["assignments"]
                assert isinstance(assignments, dict)
                fields = list(assignments)
                parameters = [assignments[field] for field in fields]
                parameters.extend([update["record_id"], update["sha256"]])
                cursor = connection.execute(
                    f"UPDATE articles SET {', '.join(f'{field} = ?' for field in fields)} "
                    "WHERE id = ? AND sha256 = ? AND lower(source) = 'local'",
                    parameters,
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(f"Terra review target changed: {update['record_id']}")
                applied.append({"record_id": update["record_id"], "fields": fields})
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("SQLite integrity check failed after Terra metadata transaction")
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
    return {
        "mode": "applied",
        "reviewed": len(reviews),
        "accepted": len(accepted),
        "title_only": title_only,
        "backup": str(backup),
        "applied": applied,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None, help="Configuration YAML path")
    parser.add_argument("--output", type=Path, required=True, help="JSON preview destination")
    parser.add_argument("--limit", type=int, default=None, help="Maximum local rows to scan")
    parser.add_argument("--ocr", action="store_true", help="OCR only native-text-poor PDF pages")
    parser.add_argument("--audit", type=Path, help="Reviewed audit JSON to apply")
    parser.add_argument(
        "--terra-review",
        type=Path,
        action="append",
        default=[],
        help="Evidence-backed Terra JSONL review to apply (repeatable)",
    )
    parser.add_argument(
        "--title-only",
        action="store_true",
        help="Apply only reviewed titles from Terra JSONL files",
    )
    parser.add_argument("--apply", action="store_true", help="Apply reviewed audit candidates")
    arguments = parser.parse_args()
    if arguments.limit is not None and arguments.limit < 1:
        parser.error("--limit must be positive")
    settings = load_settings(arguments.config)
    if arguments.apply:
        if arguments.audit is None and not arguments.terra_review:
            parser.error("--apply requires --audit or --terra-review")
        if arguments.audit is not None and arguments.terra_review:
            parser.error("choose either --audit or --terra-review")
        if arguments.title_only and not arguments.terra_review:
            parser.error("--title-only requires --terra-review")
        if arguments.ocr:
            parser.error("--ocr is only available while generating an audit")
        if arguments.terra_review:
            result = apply_terra_reviews(
                database_path=settings.paths.common_database_path,
                review_paths=arguments.terra_review,
                backup_dir=settings.paths.data_dir / "backups" / "local-metadata-audit",
                title_only=arguments.title_only,
            )
        else:
            assert arguments.audit is not None
            audit = json.loads(arguments.audit.read_text(encoding="utf-8"))
            if not isinstance(audit, dict):
                parser.error("--audit must contain a JSON object")
            result = apply_reviewed_audit(
                database_path=settings.paths.common_database_path,
                audit=audit,
                backup_dir=settings.paths.data_dir / "backups" / "local-metadata-audit",
            )
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return
    if arguments.audit is not None or arguments.terra_review or arguments.title_only:
        parser.error("--audit, --terra-review and --title-only are only valid with --apply")
    ocr_extractor = (
        WindowsOcrPdfExtractor(
            cache_dir=settings.paths.cache_dir / "windows-ocr",
            min_page_text_characters=settings.ingestion.min_page_text_characters,
            language=settings.ingestion.ocr_language,
            min_confidence=settings.ingestion.ocr_min_confidence,
        )
        if arguments.ocr
        else None
    )
    report = audit_local_pdf_metadata(
        database_path=settings.paths.common_database_path,
        extracted_dir=settings.paths.extracted_dir,
        limit=arguments.limit,
        ocr_extractor=ocr_extractor,
        audit_cache_dir=settings.paths.cache_dir / "local-metadata-audit",
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
