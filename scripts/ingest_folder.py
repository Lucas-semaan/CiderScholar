"""Sequentially ingest a local folder of PDFs and write a technical JSON report."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import load_settings
from app.database.sqlite import Database
from app.ingestion.pipeline import IngestionPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", type=Path, help="Folder containing PDFs")
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--recursive", action="store_true", help="Scan subfolders")
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop after the first failed PDF (default: continue)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    settings.paths.create()
    database = Database(settings.paths.database_path)
    database.initialize()
    pipeline = IngestionPipeline(settings, database)

    folder = (args.folder or settings.paths.pdf_dir).resolve()
    if not folder.is_dir():
        print(f"Dossier introuvable : {folder}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, settings.app.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    pattern = "**/*.pdf" if args.recursive else "*.pdf"
    pdfs = sorted(path for path in folder.glob(pattern) if path.is_file())
    reports: list[dict[str, object]] = []

    print("MODE HORS LIGNE ACTIF" if settings.app.offline_mode else "MODE EN LIGNE")
    print(f"PDF détectés : {len(pdfs)}")
    for index, pdf in enumerate(pdfs, start=1):
        print(f"[{index}/{len(pdfs)}] {pdf.name}")
        report = pipeline.ingest_file(pdf)
        reports.append(report.model_dump(mode="json"))
        print(f"  état={report.status} pages={report.page_count} fragments={report.chunk_count}")
        if report.status == "failed" and args.stop_on_error:
            break

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = settings.paths.exports_dir / f"ingestion-{timestamp}.json"
    report_path.write_text(
        json.dumps(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "offline_mode": settings.app.offline_mode,
                "pdf_count": len(reports),
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
