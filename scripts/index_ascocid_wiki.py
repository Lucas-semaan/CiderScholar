"""Index the textual Ascocid wiki archive into the authoritative local corpus.

Office documents are expected to have been converted with
``scripts/convert_text_documents.ps1``. PDF sources are ingested directly. Small
text/XML formats are rendered locally to page-addressable PDFs before ingestion.
Every successful source is registered against its original relative path so chat
citations can use ``Ascocid — <filename>`` even when the scientific corpus already
contained the same PDF.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import html
import json
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import fitz

from app.config import load_settings
from app.corpora import CorpusScope, corpus_paths, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.deduplication import sha256_file
from app.ingestion.pipeline import IngestionPipeline, PdfCatalogMetadata
from app.ingestion.windows_ocr import WindowsOcrPdfExtractor
from scripts.render_text_pdf import render_text_pdf

OFFICE_EXTENSIONS = frozenset({".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".xlsm"})
TEXT_EXTENSIONS = frozenset({".bib", ".html", ".htm", ".md", ".mm", ".ris", ".svg", ".txt"})
RASTER_EXTENSIONS = frozenset({".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff"})
INDEXABLE_EXTENSIONS = frozenset({".pdf", *OFFICE_EXTENSIONS, *TEXT_EXTENSIONS, *RASTER_EXTENSIONS})
TAG = re.compile(r"<[^>]+>")
SPACE = re.compile(r"[ \t\f\v]+")


@dataclass(frozen=True, slots=True)
class SourceDocument:
    source_path: Path
    indexed_pdf_path: Path
    conversion_status: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki-dir", type=Path, default=Path("wiki"))
    parser.add_argument(
        "--converted-dir",
        type=Path,
        default=Path("data/common/ascocid-wiki-converted"),
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="Do not retry image-only PDFs with the local Windows OCR adapter.",
    )
    return parser


def _conversion_map(converted_dir: Path) -> dict[Path, tuple[Path, str]]:
    reports = sorted(converted_dir.glob("conversion-*.json"))
    if not reports:
        return {}
    payload = json.loads(reports[-1].read_text(encoding="utf-8-sig"))
    mapped: dict[Path, tuple[Path, str]] = {}
    for item in payload.get("reports", []):
        if item.get("status") not in {"converted", "cached"}:
            continue
        source = Path(str(item["source"])).resolve()
        destination = Path(str(item["destination"])).resolve()
        if destination.is_file():
            mapped[source] = (destination, str(item["status"]))
    return mapped


def _text_from_small_source(path: Path) -> str:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    if path.suffix.casefold() in {".html", ".htm", ".mm", ".svg"}:
        raw = TAG.sub(" ", raw)
        raw = html.unescape(raw)
    lines = [SPACE.sub(" ", line).strip() for line in raw.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _render_small_source(source: Path, wiki_dir: Path, converted_dir: Path) -> Path:
    relative = source.relative_to(wiki_dir).as_posix().casefold().encode("utf-8")
    prefix = hashlib.sha256(relative).hexdigest()[:16]
    destination = converted_dir / f"{prefix}-{source.name}.pdf"
    if destination.is_file() and destination.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return destination
    text = _text_from_small_source(source)
    if not text:
        raise ValueError("text source contains no indexable text")
    render_text_pdf(text, destination, title=source.name, source_path=str(source))
    return destination


def _render_image_source(source: Path, wiki_dir: Path, converted_dir: Path) -> Path:
    relative = source.relative_to(wiki_dir).as_posix().casefold().encode("utf-8")
    prefix = hashlib.sha256(relative).hexdigest()[:16]
    destination = converted_dir / f"{prefix}-{source.name}.pdf"
    if destination.is_file() and destination.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(source) as image_document:
        pdf_bytes = image_document.convert_to_pdf()
    with fitz.open("pdf", pdf_bytes) as pdf_document:
        pdf_document.set_metadata(
            {
                "title": source.name,
                "subject": f"Converted from {source}",
                "creator": "CiderScholar local Ascocid image converter",
            }
        )
        pdf_document.save(destination, garbage=4, deflate=True)
    return destination


def discover_sources(
    wiki_dir: Path, converted_dir: Path
) -> tuple[list[SourceDocument], list[Path]]:
    converted = _conversion_map(converted_dir)
    selected: list[SourceDocument] = []
    unsupported: list[Path] = []
    for source in sorted(path for path in wiki_dir.rglob("*") if path.is_file()):
        relative = source.relative_to(wiki_dir)
        # The root pages and ``wiki/sources`` are the curated reasoning layer,
        # not primary evidence. Only documents stored in the raw archive
        # subdirectories become citable Ascocid sources.
        if len(relative.parts) == 1 or relative.parts[0].casefold() == "sources":
            continue
        extension = source.suffix.casefold()
        if extension == ".pdf":
            selected.append(SourceDocument(source, source, "native_pdf"))
        elif extension in OFFICE_EXTENSIONS:
            mapped = converted.get(source.resolve())
            if mapped is None:
                unsupported.append(source)
            else:
                selected.append(SourceDocument(source, mapped[0], mapped[1]))
        elif extension in TEXT_EXTENSIONS:
            try:
                rendered = _render_small_source(source, wiki_dir, converted_dir)
            except (OSError, UnicodeError, ValueError):
                unsupported.append(source)
            else:
                selected.append(SourceDocument(source, rendered, "rendered_text"))
        elif extension in RASTER_EXTENSIONS:
            try:
                rendered = _render_image_source(source, wiki_dir, converted_dir)
            except (OSError, RuntimeError, ValueError, fitz.FileDataError):
                unsupported.append(source)
            else:
                selected.append(SourceDocument(source, rendered, "rendered_image"))
        elif extension and extension not in INDEXABLE_EXTENSIONS:
            unsupported.append(source)
    return selected, unsupported


def _backup_sqlite(database_path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.unlink(missing_ok=True)
    with (
        closing(sqlite3.connect(database_path)) as source,
        closing(sqlite3.connect(temporary)) as backup,
    ):
        source.backup(backup)
    with closing(sqlite3.connect(f"file:{temporary.as_posix()}?mode=ro", uri=True)) as check:
        if str(check.execute("PRAGMA integrity_check").fetchone()[0]).casefold() != "ok":
            raise RuntimeError("Ascocid pre-index SQLite backup failed integrity verification")
    temporary.replace(destination)


def _document_id(relative_path: str) -> str:
    digest = hashlib.sha256(relative_path.casefold().encode("utf-8")).hexdigest()[:24]
    return f"ascocid-wiki-{digest}"


def _release_document_memory() -> None:
    fitz.TOOLS.store_shrink(100)
    gc.collect()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    wiki_dir = args.wiki_dir.resolve()
    converted_dir = args.converted_dir.resolve()
    if not wiki_dir.is_dir():
        print(f"Dossier wiki introuvable : {wiki_dir}", file=sys.stderr)
        return 2
    converted_dir.mkdir(parents=True, exist_ok=True)

    settings = settings_for_corpus(load_settings(args.config), CorpusScope.COMMON)
    settings.paths.create()
    database_path = corpus_paths(settings, CorpusScope.COMMON).database_path
    selected, unsupported = discover_sources(wiki_dir, converted_dir)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = settings.paths.exports_dir / "ascocid-wiki-index" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    backup_path = run_dir / "science-rag-before.sqlite3"
    if database_path.is_file():
        _backup_sqlite(database_path, backup_path)

    database = Database(database_path)
    database.initialize()
    indexed_by_path = {str(row["relative_path"]): row for row in database.ascocid_wiki_documents()}

    pipeline = IngestionPipeline(settings, database)
    ocr_pipeline = None
    if not args.no_ocr:
        ocr_pipeline = IngestionPipeline(
            settings,
            database,
            extractor=WindowsOcrPdfExtractor(
                cache_dir=settings.paths.cache_dir / "windows-ocr",
                min_page_text_characters=settings.ingestion.min_page_text_characters,
                language=settings.ingestion.ocr_language,
                min_confidence=settings.ingestion.ocr_min_confidence,
            ),
            refresh_ocr_cache=True,
        )

    reports: list[dict[str, object]] = []
    for index, document in enumerate(selected, start=1):
        relative_path = document.source_path.relative_to(wiki_dir).as_posix()
        print(f"[{index}/{len(selected)}] {relative_path}", flush=True)
        source_sha256 = sha256_file(document.source_path)
        existing = indexed_by_path.get(relative_path)
        if existing is not None and str(existing["source_sha256"]) == source_sha256:
            reports.append(
                {
                    "status": "already_indexed",
                    "article_id": str(existing["article_id"]),
                    "source_path": str(document.source_path),
                    "relative_path": relative_path,
                    "source_sha256": source_sha256,
                    "conversion_status": document.conversion_status,
                }
            )
            print(f"  état=already_indexed article={existing['article_id']}", flush=True)
            continue
        indexed_sha256 = sha256_file(document.indexed_pdf_path)
        report = pipeline.ingest_file(
            document.indexed_pdf_path,
            catalog_metadata=PdfCatalogMetadata(
                title=document.source_path.name,
                authors=["Ascocid"],
                journal="Ascocid",
                work_type="internal_document",
                publisher="Ascocid",
                source="ascocid_wiki",
            ),
            precomputed_sha256=indexed_sha256,
        )
        if report.status == "ocr_required" and ocr_pipeline is not None:
            report = ocr_pipeline.ingest_file(
                document.indexed_pdf_path,
                catalog_metadata=PdfCatalogMetadata(
                    title=document.source_path.name,
                    authors=["Ascocid"],
                    journal="Ascocid",
                    work_type="internal_document",
                    publisher="Ascocid",
                    source="ascocid_wiki",
                ),
                precomputed_sha256=indexed_sha256,
            )
        payload = report.model_dump(mode="json")
        payload.update(
            {
                "source_path": str(document.source_path),
                "relative_path": relative_path,
                "source_sha256": source_sha256,
                "conversion_status": document.conversion_status,
            }
        )
        reports.append(payload)
        if report.article_id is None or report.status not in {"chunks_ready", "duplicate"}:
            print(f"  état={report.status}", flush=True)
            _release_document_memory()
            continue
        database.upsert_ascocid_wiki_document(
            document_id=_document_id(relative_path),
            relative_path=relative_path,
            filename=document.source_path.name,
            source_sha256=source_sha256,
            article_id=report.article_id,
            indexed_file_path=str(document.indexed_pdf_path),
            indexed_file_sha256=indexed_sha256,
        )
        print(f"  état={report.status} article={report.article_id}", flush=True)
        _release_document_memory()

    status_counts: dict[str, int] = {}
    for report in reports:
        status = str(report["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "wiki_dir": str(wiki_dir),
        "converted_dir": str(converted_dir),
        "backup_path": str(backup_path),
        "selected_document_count": len(selected),
        "unsupported_asset_count": len(unsupported),
        "unsupported_assets": [str(path.relative_to(wiki_dir)) for path in unsupported],
        "status_counts": status_counts,
        "reports": reports,
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Rapport : {report_path}", flush=True)
    return 1 if status_counts.get("failed", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
