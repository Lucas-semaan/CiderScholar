from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import fitz

from scripts.audit_local_pdf_metadata import (
    apply_reviewed_audit,
    apply_terra_reviews,
    audit_local_pdf_metadata,
)


def test_audit_previews_unavailable_local_pdf_without_writing_database(tmp_path: Path) -> None:
    database_path = tmp_path / "corpus.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE articles (
                id TEXT, sha256 TEXT, doi TEXT, title TEXT, publication_year INTEGER,
                pdf_path TEXT, source TEXT, created_at TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "record-1",
                "a" * 64,
                None,
                "Slide 1",
                2014,
                str(tmp_path / "not-present.pdf"),
                "local",
                "2026-09-01T00:00:00Z",
            ),
        )

    report = audit_local_pdf_metadata(
        database_path=database_path,
        extracted_dir=tmp_path / "extracted",
        limit=None,
    )

    assert report["mode"] == "preview_only"
    assert report["inspected_local_records"] == 1
    assert report["pdf_unavailable"] == 1
    candidate = report["candidates"][0]
    assert candidate["record_id"] == "record-1"
    assert candidate["current_title"] == "Slide 1"
    assert candidate["proposed_title"] == "fichier local"
    assert candidate["title_reason"] == "pdf_unavailable"
    assert candidate["current_year"] == 2014
    assert candidate["proposed_year"] is None
    assert candidate["accepted"] is False
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT title FROM articles").fetchone()[0] == "Slide 1"

    result = apply_reviewed_audit(
        database_path=database_path,
        audit=report,
        backup_dir=tmp_path / "backups",
    )

    assert len(result["applied"]) == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT title FROM articles").fetchone()[0] == "fichier local"


def test_apply_uses_exact_doi_metadata_and_creates_verified_backup(tmp_path: Path) -> None:
    database_path = tmp_path / "corpus.sqlite3"
    pdf_path = tmp_path / "source.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Validated apple fermentation article")
        document.save(pdf_path)
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE articles (
                id TEXT, sha256 TEXT, doi TEXT, title TEXT, publication_year INTEGER,
                pdf_path TEXT, source TEXT, created_at TEXT
            );
            CREATE TABLE bibliographic_records (
                id TEXT, doi TEXT, title TEXT, publication_year INTEGER,
                relevance_status TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "record-1",
                "b" * 64,
                "10.1000/example",
                "untitled",
                2015,
                str(pdf_path),
                "local",
                "2026-09-01T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO bibliographic_records VALUES (?, ?, ?, ?, ?)",
            (
                "bib-1",
                "10.1000/example",
                "Validated apple fermentation article",
                2017,
                "accepted",
            ),
        )

    report = audit_local_pdf_metadata(
        database_path=database_path,
        extracted_dir=tmp_path / "extracted",
        limit=None,
    )
    result = apply_reviewed_audit(
        database_path=database_path,
        audit=report,
        backup_dir=tmp_path / "backups",
    )

    assert len(result["applied"]) == 1
    assert Path(str(result["backup"])).is_file()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT title, publication_year FROM articles WHERE id = 'record-1'"
        ).fetchone() == ("Validated apple fermentation article", 2017)


def test_apply_terra_review_updates_document_metadata_with_evidence(tmp_path: Path) -> None:
    database_path = tmp_path / "corpus.sqlite3"
    review_path = tmp_path / "terra.jsonl"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE articles (
                id TEXT, sha256 TEXT, title TEXT, authors TEXT, publication_year INTEGER,
                work_type TEXT, source TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("record-1", "c" * 64, "Slide 1", "[]", 2099, None, "local"),
        )
    review = {
        "record_id": "record-1",
        "sha256": "c" * 64,
        "current": {"title": "Slide 1", "authors": [], "year": 2099, "work_type": None},
        "proposed": {
            "title": "Guide pratique de fermentation cidricole",
            "authors": ["Institut du cidre"],
            "year": 2024,
            "work_type": "report",
        },
        "confidence": "high",
        "accepted": True,
        "evidence": [
            {
                "source": "ocr",
                "page": 1,
                "note": "Couverture et mentions de responsabilité",
            }
        ],
        "ambiguity": None,
    }
    review_path.write_text(json.dumps(review, ensure_ascii=False) + "\n", encoding="utf-8")

    result = apply_terra_reviews(
        database_path=database_path,
        review_paths=[review_path],
        backup_dir=tmp_path / "backups",
    )

    assert result["mode"] == "applied"
    assert Path(str(result["backup"])).is_file()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT title, authors, publication_year, work_type FROM articles"
        ).fetchone() == (
            "Guide pratique de fermentation cidricole",
            '["Institut du cidre"]',
            2024,
            "report",
        )


def test_apply_terra_review_rejects_accepted_decision_without_evidence(tmp_path: Path) -> None:
    database_path = tmp_path / "corpus.sqlite3"
    review_path = tmp_path / "terra.jsonl"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE articles (
                id TEXT, sha256 TEXT, title TEXT, authors TEXT, publication_year INTEGER,
                work_type TEXT, source TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("record-1", "d" * 64, "Untitled", "[]", None, None, "local"),
        )
    review = {
        "record_id": "record-1",
        "sha256": "d" * 64,
        "current": {"title": "Untitled", "authors": [], "year": None, "work_type": None},
        "proposed": {
            "title": "Un titre apparemment crédible",
            "authors": [],
            "year": None,
            "work_type": "other",
        },
        "confidence": "high",
        "accepted": True,
        "evidence": [],
        "ambiguity": None,
    }
    review_path.write_text(json.dumps(review, ensure_ascii=False) + "\n", encoding="utf-8")

    try:
        apply_terra_reviews(
            database_path=database_path,
            review_paths=[review_path],
            backup_dir=tmp_path / "backups",
        )
    except ValueError as error:
        assert "lacks usable evidence" in str(error)
    else:
        raise AssertionError("an accepted review without evidence must be rejected")

    assert not (tmp_path / "backups").exists()


def test_apply_terra_review_does_not_replace_existing_authors_with_variants(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "corpus.sqlite3"
    review_path = tmp_path / "terra.jsonl"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE articles (
                id TEXT, sha256 TEXT, title TEXT, authors TEXT, publication_year INTEGER,
                work_type TEXT, source TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "record-1",
                "e" * 64,
                "A reliable publication title",
                '["Ada Martin"]',
                2020,
                "article",
                "local",
            ),
        )
    review = {
        "record_id": "record-1",
        "sha256": "e" * 64,
        "current": {
            "title": "A reliable publication title",
            "authors": ["Ada Martin"],
            "year": 2020,
            "work_type": "article",
        },
        "proposed": {
            "title": "A reliable publication title",
            "authors": ["A. Martin", "Ada Martin"],
            "year": 2020,
            "work_type": "journal_article",
        },
        "confidence": "high",
        "accepted": True,
        "evidence": [{"source": "native_metadata", "page": None, "note": "Exact DOI"}],
        "ambiguity": None,
    }
    review_path.write_text(json.dumps(review) + "\n", encoding="utf-8")

    result = apply_terra_reviews(
        database_path=database_path,
        review_paths=[review_path],
        backup_dir=tmp_path / "backups",
    )

    assert result["mode"] == "no_changes"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT authors FROM articles").fetchone()[0] == '["Ada Martin"]'


def test_apply_terra_review_explicitly_replaces_wrong_native_pdf_author(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "corpus.sqlite3"
    review_path = tmp_path / "terra.jsonl"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE articles (
                id TEXT, sha256 TEXT, title TEXT, authors TEXT, publication_year INTEGER,
                work_type TEXT, source TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "record-1",
                "f" * 64,
                "A reliable report title",
                '["Microsoft Word user"]',
                2020,
                "report",
                "local",
            ),
        )
    review = {
        "record_id": "record-1",
        "sha256": "f" * 64,
        "current": {
            "title": "A reliable report title",
            "authors": ["Microsoft Word user"],
            "year": 2020,
            "work_type": "report",
        },
        "proposed": {
            "title": "A reliable report title",
            "authors": ["Institut du cidre"],
            "year": 2020,
            "work_type": "report",
        },
        "confidence": "high",
        "accepted": True,
        "replace_authors": True,
        "evidence": [
            {
                "source": "native_text",
                "page": 1,
                "note": "Responsibility statement on the cover",
            }
        ],
        "ambiguity": None,
    }
    review_path.write_text(json.dumps(review) + "\n", encoding="utf-8")

    apply_terra_reviews(
        database_path=database_path,
        review_paths=[review_path],
        backup_dir=tmp_path / "backups",
    )

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT authors FROM articles").fetchone()[0] == (
            '["Institut du cidre"]'
        )
