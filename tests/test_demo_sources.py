from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.demo_sources import DemoSourceVerificationError, verify_demo_sources


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"verified source")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    database = tmp_path / "common.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE articles(
                id TEXT, title TEXT, doi TEXT, sha256 TEXT, pdf_path TEXT
            );
            CREATE TABLE chunks(
                article_id TEXT, page_start INTEGER, page_end INTEGER, text TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO articles VALUES ('a', 'Source', '10.demo/source', ?, ?)",
            (digest, str(pdf)),
        )
        connection.execute("INSERT INTO chunks VALUES ('a', 2, 3, 'Expected evidence')")
    source = {
        "doi": "10.demo/source",
        "title": "Source",
        "sha256": digest,
        "evidence": [{"page": 2, "term": "expected evidence"}],
    }
    manifest = tmp_path / "questions.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "questions": [
                    {"id": "one", "kind": "direct", "sources": [source]},
                    {"id": "two", "kind": "comparative", "sources": [source, source]},
                    {"id": "three", "kind": "follow_up", "sources": [source]},
                ],
            }
        ),
        encoding="utf-8",
    )
    return database, manifest, pdf


def test_demo_sources_verify_pdf_metadata_and_page_evidence(tmp_path: Path) -> None:
    database, manifest, _ = _fixture(tmp_path)

    report = verify_demo_sources(database, manifest)

    assert report["state"] == "ready"
    assert report["question_count"] == 3
    assert report["source_check_count"] == 4


def test_demo_sources_detect_a_modified_corpus_file(tmp_path: Path) -> None:
    database, manifest, pdf = _fixture(tmp_path)
    pdf.write_bytes(b"modified")

    with pytest.raises(DemoSourceVerificationError, match="PDF hash changed"):
        verify_demo_sources(database, manifest)
