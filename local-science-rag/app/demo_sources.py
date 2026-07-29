"""Versioned local verification for the non-sensitive demonstration questions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


class DemoSourceVerificationError(RuntimeError):
    """A versioned demonstration source no longer matches the local corpus."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def verify_demo_sources(database_path: Path, manifest_path: Path) -> dict[str, object]:
    """Verify source metadata, PDFs and page evidence without invoking retrieval or ARGO."""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        questions = manifest["questions"]
    except (OSError, KeyError, json.JSONDecodeError, TypeError) as exc:
        raise DemoSourceVerificationError("demo question manifest is invalid") from exc
    if manifest.get("schema_version") != 1 or not isinstance(questions, list):
        raise DemoSourceVerificationError("unsupported demo question manifest")
    kinds = {question.get("kind") for question in questions if isinstance(question, dict)}
    if len(questions) != 3 or kinds != {"direct", "comparative", "follow_up"}:
        raise DemoSourceVerificationError(
            "demo must contain direct, comparative and follow-up cases"
        )

    source_checks: list[dict[str, object]] = []
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        for question in questions:
            if not isinstance(question, dict) or not isinstance(question.get("sources"), list):
                raise DemoSourceVerificationError("demo question source list is invalid")
            for source in question["sources"]:
                source_checks.append(_verify_source(connection, question, source))
    return {
        "state": "ready",
        "schema_version": 1,
        "question_count": len(questions),
        "source_check_count": len(source_checks),
        "sources": source_checks,
    }


def _verify_source(
    connection: sqlite3.Connection,
    question: dict[str, object],
    source: object,
) -> dict[str, object]:
    if not isinstance(source, dict):
        raise DemoSourceVerificationError("demo source entry is invalid")
    doi = str(source.get("doi", "")).casefold()
    row = connection.execute(
        "SELECT id, title, doi, sha256, pdf_path FROM articles WHERE lower(doi) = ?",
        (doi,),
    ).fetchone()
    if row is None:
        raise DemoSourceVerificationError(f"demo DOI is absent from corpus: {doi}")
    if row["title"] != source.get("title") or row["sha256"] != source.get("sha256"):
        raise DemoSourceVerificationError(f"demo source metadata changed: {doi}")
    pdf_path = Path(row["pdf_path"])
    if not pdf_path.is_file() or _sha256(pdf_path) != source.get("sha256"):
        raise DemoSourceVerificationError(f"demo source PDF hash changed: {doi}")
    evidence = source.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise DemoSourceVerificationError(f"demo evidence is absent: {doi}")
    for expected in evidence:
        if not isinstance(expected, dict):
            raise DemoSourceVerificationError(f"demo evidence entry is invalid: {doi}")
        page = int(expected.get("page", 0))
        term = str(expected.get("term", ""))
        matching = connection.execute(
            """
            SELECT 1 FROM chunks
            WHERE article_id = ? AND page_start <= ? AND page_end >= ?
              AND instr(lower(text), lower(?)) > 0
            LIMIT 1
            """,
            (row["id"], page, page, term),
        ).fetchone()
        if matching is None:
            raise DemoSourceVerificationError(
                f"demo evidence changed for {doi} at page {page}: {term}"
            )
    return {
        "question_id": question.get("id"),
        "doi": doi,
        "sha256": row["sha256"],
        "evidence_count": len(evidence),
    }
