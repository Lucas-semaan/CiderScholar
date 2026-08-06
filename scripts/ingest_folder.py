"""Sequentially ingest a local folder of PDFs and write a technical JSON report."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import load_settings
from app.corpora import CorpusScope, corpus_paths, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.prefilter import ExistingCorpusMatcher
from app.ingestion.windows_ocr import WindowsOcrPdfExtractor
from app.services.workflows import ingest_paths, pdf_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", type=Path, help="Folder containing PDFs")
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--recursive", action="store_true", help="Scan subfolders")
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="Run local Windows OCR when embedded PDF text is insufficient",
    )
    parser.add_argument(
        "--skip-known",
        action="store_true",
        help="Skip durable SHA/DOI duplicates already present in searchable corpora",
    )
    parser.add_argument(
        "--wait-for-memory",
        action="store_true",
        help="Pause and retry the current PDF instead of advancing under memory pressure",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop after the first failed PDF (default: continue)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    base_settings = load_settings(args.config)
    settings = settings_for_corpus(base_settings, CorpusScope.COMMON)
    settings.paths.create()
    database = Database(settings.paths.database_path)
    database.initialize()

    folder = (args.folder or settings.paths.pdf_dir).resolve()
    if not folder.is_dir():
        print(f"Dossier introuvable : {folder}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, settings.app.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    pdfs = list(pdf_paths(folder, recursive=args.recursive))
    selected_pdfs = pdfs
    precomputed_sha256: dict[Path, str] | None = None
    prefilter_matches: list[dict[str, object]] = []
    title_candidate_count = 0
    if args.skip_known:
        known_databases = {
            CorpusScope.COMMON.value: corpus_paths(base_settings, CorpusScope.COMMON).database_path
        }
        matcher = ExistingCorpusMatcher.from_databases(
            known_databases,
            scan_pages=settings.ingestion.metadata_scan_pages,
        )
        selected_pdfs = []
        precomputed_sha256 = {}
        for index, pdf in enumerate(pdfs, start=1):
            print(f"Préfiltre [{index}/{len(pdfs)}] {pdf.name}")
            preflight = matcher.inspect(pdf)
            if preflight.match is not None:
                prefilter_matches.append(
                    {
                        "pdf_path": str(pdf),
                        "status": "duplicate",
                        "matched_scope": preflight.match.scope,
                        "matched_article_id": preflight.match.article_id,
                        "match_reason": preflight.match.reason,
                        "doi": preflight.match.doi,
                        "title": preflight.match.title,
                    }
                )
                print(
                    f"  état=duplicate portée={preflight.match.scope} "
                    f"raison={preflight.match.reason}"
                )
                continue
            if preflight.title_candidate is not None:
                title_candidate_count += 1
                print("  titre déjà vu, conservé faute de SHA/DOI identique")
            selected_pdfs.append(pdf)
            if preflight.sha256 is not None:
                precomputed_sha256[pdf] = preflight.sha256
    ocr_extractor = (
        WindowsOcrPdfExtractor(
            cache_dir=settings.paths.cache_dir / "windows-ocr",
            min_page_text_characters=settings.ingestion.min_page_text_characters,
            language=settings.ingestion.ocr_language,
            min_confidence=settings.ingestion.ocr_min_confidence,
        )
        if args.ocr
        else None
    )

    print("MODE HORS LIGNE ACTIF" if settings.app.offline_mode else "MODE EN LIGNE")
    print(f"PDF détectés : {len(pdfs)}")
    if args.skip_known:
        print(
            f"PDF déjà présents : {len(prefilter_matches)} ; PDF à ingérer : {len(selected_pdfs)}"
        )

    def progress(completed: int, total: int, name: str, state: str) -> None:
        if state == "ingestion":
            print(f"[{completed + 1}/{total}] {name}")
        elif state == "ocr":
            print("  OCR Windows local")
        else:
            print(f"  état={state}")

    ingestion_reports = ingest_paths(
        settings,
        database,
        selected_pdfs,
        progress=progress,
        ocr_extractor=ocr_extractor,
        stop_on_error=args.stop_on_error,
        precomputed_sha256=precomputed_sha256,
        memory_retry_attempts=60 if args.wait_for_memory else 0,
    )
    reports = [report.model_dump(mode="json") for report in ingestion_reports]

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = settings.paths.exports_dir / f"ingestion-{timestamp}.json"
    report_path.write_text(
        json.dumps(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "offline_mode": settings.app.offline_mode,
                "corpus": CorpusScope.COMMON.value,
                "ocr_enabled": args.ocr,
                "skip_known_enabled": args.skip_known,
                "wait_for_memory_enabled": args.wait_for_memory,
                "pdf_count": len(pdfs),
                "ingested_pdf_count": len(reports),
                "skipped_known_count": len(prefilter_matches),
                "title_candidate_count": title_candidate_count,
                "prefilter_matches": prefilter_matches,
                "reports": reports,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Rapport : {report_path}")
    return 1 if any(report["status"] == "failed" for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
