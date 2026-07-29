"""Import the official IFPC technical catalog and targeted author bibliography."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx

from app.config import Settings, load_settings
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.ingestion.pipeline import IngestionPipeline, PdfCatalogMetadata
from app.ingestion.windows_ocr import WindowsOcrPdfExtractor
from app.services.workflows import index_pending_chunks
from app.updates.cleanup import archive_and_purge_rejected_records
from app.updates.harvest import BibliographicHarvestStore, CiderPilotHarvester
from app.updates.vector_index import BibliographicVectorIndex, index_bibliographic_abstracts

IFPC_CATALOG_URL = "https://www.ifpc.eu/cahiers-techniques/"
IFPC_AUTHOR_QUERIES: tuple[dict[str, str], ...] = (
    {
        "polyphenols": '"Pascal Poupard" (cider OR cidre OR "apple juice")',
        "microbiologie": '"Hugues Guichard" (cider OR cidre OR calvados)',
        "aromes_procede": '"Rémi Bauduin" (cider OR cidre OR "apple juice")',
        "jus_pomme": '"Institut Français des Productions Cidricoles"',
    },
)
IFPC_ARCHIVE_FALLBACKS = {
    "https://www.ifpc.eu/fileadmin/users/ifpc/infos_techniques/Art_couleur_JT_pulve.pdf": (
        "https://web.archive.org/web/20201230113020id_/http://www.ifpc.eu/"
        "fileadmin/users/ifpc/infos_techniques/Art_couleur_JT_pulve.pdf"
    )
}
IFPC_BIBLIOGRAPHY_PROFILE = "ifpc_scientists_v2"
USER_AGENT = "CiderScholar/0.1 (IFPC research import; contact: lucas.semaan@inrae.fr)"
YEAR_PATTERN = re.compile(r"\b((?:19|20)\d{2})\b")
SAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
MAX_PDF_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class IfpcPublication:
    title: str
    url: str
    publication_year: int | None


class _IfpcCatalogParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.publications: list[IfpcPublication] = []
        self._last_year: int | None = None
        self._href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        self._href = dict(attrs).get("href")
        self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._anchor_text.append(data)
            return
        match = YEAR_PATTERN.search(data)
        if match:
            self._last_year = int(match.group(1))

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._href is None:
            return
        title = " ".join("".join(self._anchor_text).split())
        url = _canonical_ifpc_pdf_url(self.base_url, self._href)
        if title and url:
            self.publications.append(
                IfpcPublication(
                    title=title,
                    url=url,
                    publication_year=self._last_year,
                )
            )
        self._href = None
        self._anchor_text = []


def _canonical_ifpc_pdf_url(base_url: str, href: str) -> str | None:
    candidate = urlsplit(urljoin(base_url, href.strip()))
    if candidate.hostname not in {"ifpc.eu", "www.ifpc.eu"}:
        return None
    if not candidate.path.casefold().endswith(".pdf"):
        return None
    return urlunsplit(("https", "www.ifpc.eu", candidate.path, candidate.query, ""))


def parse_ifpc_catalog(html: str, *, base_url: str = IFPC_CATALOG_URL) -> list[IfpcPublication]:
    parser = _IfpcCatalogParser(base_url)
    parser.feed(html)
    unique: dict[str, IfpcPublication] = {}
    for publication in parser.publications:
        unique.setdefault(publication.url, publication)
    return list(unique.values())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between PDF requests")
    parser.add_argument("--limit", type=int, help="Optional PDF limit for a diagnostic run")
    parser.add_argument("--skip-pdfs", action="store_true")
    parser.add_argument("--skip-bibliography", action="store_true")
    parser.add_argument(
        "--bibliography-pages",
        type=int,
        default=2,
        help="Result pages collected per provider and author query (default: 2)",
    )
    parser.add_argument("--no-ocr", action="store_true", help="Leave image-only PDFs pending")
    parser.add_argument("--no-index", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print the complete JSON report")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not 0.25 <= args.delay <= 60:
        raise ValueError("delay must be between 0.25 and 60 seconds")
    if args.limit is not None and not 1 <= args.limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if not 1 <= args.bibliography_pages <= 5:
        raise ValueError("bibliography pages must be between 1 and 5")

    settings = load_settings(args.config)
    settings.paths.create()
    database = Database(settings.paths.database_path)
    database.initialize()

    pdf_payload: dict[str, object] | None = None
    new_article_ids: list[str] = []
    if not args.skip_pdfs:
        pdf_payload, new_article_ids = _import_official_pdfs(
            settings,
            database,
            delay=args.delay,
            limit=args.limit,
            run_ocr=not args.no_ocr,
        )

    bibliography_payload: dict[str, object] | None = None
    if not args.skip_bibliography:
        bibliography_payload = _import_author_bibliography(
            settings,
            database,
            pages=args.bibliography_pages,
        )

    fulltext_index: dict[str, object] | None = None
    bibliography_index: dict[str, object] | None = None
    store = BibliographicHarvestStore(database)
    if not args.no_index:
        if new_article_ids:
            fulltext_index = index_pending_chunks(
                settings,
                database,
                article_ids=new_article_ids,
            ).model_dump(mode="json")
        if store.pending_abstracts(limit=1):
            backend = SentenceTransformerBackend(settings)
            bibliography_index = index_bibliographic_abstracts(
                settings,
                store,
                backend,
            ).model_dump(mode="json")

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "catalog_url": IFPC_CATALOG_URL,
        "pdf_import": pdf_payload,
        "bibliography_import": bibliography_payload,
        "fulltext_index": fulltext_index,
        "bibliography_index": bibliography_index,
        "bibliographic_statistics": store.statistics(),
        "local_pdf_articles": len(database.list_articles(limit=5000)),
    }
    report_path = _write_report(settings.paths.exports_dir, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if pdf_payload:
            print(
                f"pdfs=selected:{pdf_payload['selected']} "
                f"ingestion:{pdf_payload['ingestion']} errors:{len(pdf_payload['errors'])}"
            )
        if bibliography_payload:
            print(f"bibliography={bibliography_payload['harvest']}")
        print(f"statistics={store.statistics()}")
    print(f"report={report_path.resolve()}")
    return 0


def _import_official_pdfs(
    settings: Settings,
    database: Database,
    *,
    delay: float,
    limit: int | None,
    run_ocr: bool,
) -> tuple[dict[str, object], list[str]]:
    target_dir = settings.paths.pdf_dir / "ifpc" / "cahiers-techniques"
    target_dir.mkdir(parents=True, exist_ok=True)
    pipeline = IngestionPipeline(settings, database)
    ocr_pipeline = (
        IngestionPipeline(
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
        if run_ocr
        else None
    )
    download_counts = {"downloaded": 0, "cached": 0}
    ingestion_counts: dict[str, int] = {}
    errors: list[dict[str, str]] = []
    reports: list[dict[str, object]] = []
    new_article_ids: list[str] = []

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=60,
        follow_redirects=True,
        trust_env=False,
    ) as client:
        response = client.get(IFPC_CATALOG_URL)
        response.raise_for_status()
        catalog = parse_ifpc_catalog(response.text)
        selected = catalog[:limit] if limit is not None else catalog
        last_request = 0.0
        for index, publication in enumerate(selected, start=1):
            destination = target_dir / _publication_file_name(publication)
            try:
                if _is_pdf(destination):
                    download_state = "cached"
                else:
                    remaining = last_request + delay - time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                    source_url = IFPC_ARCHIVE_FALLBACKS.get(publication.url, publication.url)
                    _download_pdf(client, source_url, destination)
                    last_request = time.monotonic()
                    download_state = "downloaded"
                download_counts[download_state] += 1
                catalog_metadata = PdfCatalogMetadata(
                    title=publication.title,
                    journal="Cahier technique IFPC — Pomme à Cidre",
                    publication_year=publication.publication_year,
                    source="IFPC",
                )
                report = pipeline.ingest_file(
                    destination,
                    catalog_metadata=catalog_metadata,
                )
                if report.status == "ocr_required" and ocr_pipeline is not None:
                    report = ocr_pipeline.ingest_file(
                        destination,
                        catalog_metadata=catalog_metadata,
                    )
                ingestion_counts[report.status] = ingestion_counts.get(report.status, 0) + 1
                if report.status == "chunks_ready" and report.article_id:
                    new_article_ids.append(report.article_id)
                reports.append(
                    {
                        "title": publication.title,
                        "catalog_url": publication.url,
                        "download_url": source_url if download_state == "downloaded" else None,
                        "download_state": download_state,
                        **report.model_dump(mode="json"),
                    }
                )
                print(
                    f"pdf={index}/{len(selected)} download={download_state} "
                    f"ingestion={report.status} title={publication.title}",
                    flush=True,
                )
            except Exception as exc:
                errors.append(
                    {
                        "title": publication.title,
                        "url": publication.url,
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:500],
                    }
                )
                print(f"pdf={index}/{len(selected)} error={type(exc).__name__}", flush=True)
    return (
        {
            "discovered": len(catalog),
            "selected": len(selected),
            "downloads": download_counts,
            "ingestion": ingestion_counts,
            "errors": errors,
            "reports": reports,
        },
        new_article_ids,
    )


def _import_author_bibliography(
    settings: Settings,
    database: Database,
    *,
    pages: int,
) -> dict[str, object]:
    active = settings.model_copy(deep=True)
    active.harvest.profile = IFPC_BIBLIOGRAPHY_PROFILE
    active.harvest.per_source_limit = 50
    active.harvest.max_records_per_run = 1000
    store = BibliographicHarvestStore(database)
    completed_pages = store.completed_run_count(active.harvest.profile)
    harvest_runs: list[dict[str, object]] = []
    for _ in range(max(0, pages - completed_pages)):
        harvest_runs.append(
            CiderPilotHarvester(
                active,
                database,
                query_waves=IFPC_AUTHOR_QUERIES,
            )
            .run(force=True)
            .model_dump(mode="json")
        )
    if harvest_runs:
        state = (
            "completed" if all(run["state"] == "completed" for run in harvest_runs) else "partial"
        )
        harvest: dict[str, object] = {"state": state, "runs": harvest_runs}
    else:
        harvest = {
            "state": "skipped",
            "reason": f"{completed_pages} page(s) already completed",
        }
    doi_duplicate_ids = store.merge_doi_enrichment_duplicates()
    with BibliographicVectorIndex(active) as vector_index:
        duplicate_vectors_deleted = vector_index.delete(doi_duplicate_ids)
    normalized = store.normalize_existing_text()
    reclassified = store.reclassify_existing()
    abstractless_rejected = store.reject_abstractless_records()
    cleanup = archive_and_purge_rejected_records(active, database)
    return {
        "harvest": harvest,
        "doi_duplicates_merged": len(doi_duplicate_ids),
        "duplicate_vectors_deleted": duplicate_vectors_deleted,
        "normalized_records": normalized,
        "reclassified_hits": reclassified,
        "abstractless_rejected": abstractless_rejected,
        "cleanup": cleanup.model_dump(mode="json"),
    }


def _download_pdf(client: httpx.Client, url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{destination.stem}.",
        suffix=".part",
        dir=destination.parent,
    )
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as output, client.stream("GET", url) as response:
            response.raise_for_status()
            if response.url.host not in {"ifpc.eu", "www.ifpc.eu", "web.archive.org"}:
                raise ValueError("PDF download redirected outside the approved hosts")
            length = response.headers.get("content-length")
            if length and int(length) > MAX_PDF_BYTES:
                raise ValueError("PDF exceeds the 100 MB download limit")
            for chunk in response.iter_bytes(1024 * 1024):
                total += len(chunk)
                if total > MAX_PDF_BYTES:
                    raise ValueError("PDF exceeds the 100 MB download limit")
                output.write(chunk)
        temporary = Path(temporary_name)
        if not _is_pdf(temporary):
            raise ValueError("downloaded response is not a valid PDF")
        temporary.replace(destination)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _is_pdf(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 5:
        return False
    with path.open("rb") as handle:
        return handle.read(5) == b"%PDF-"


def _publication_file_name(publication: IfpcPublication) -> str:
    raw_name = Path(unquote(urlsplit(publication.url).path)).name
    cleaned = SAFE_FILE_NAME.sub("-", raw_name).strip("-.")
    return cleaned if cleaned.casefold().endswith(".pdf") else f"{cleaned}.pdf"


def _write_report(exports_dir: Path, report: dict[str, object]) -> Path:
    exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = exports_dir / f"ifpc-publications-import-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
