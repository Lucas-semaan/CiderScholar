"""Collect up to 40,000 Aureli cider article candidates into the local corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.updates.aureli import AureliClient, aureli_max_offset
from app.updates.cleanup import archive_and_purge_rejected_records
from app.updates.harvest import (
    CIDER_PILOT_THEMES,
    BibliographicHarvestStore,
    assess_cider_relevance_across_themes,
)
from app.updates.models import BibliographicRecord
from app.updates.vector_index import (
    index_bibliographic_abstracts,
    verify_bibliographic_abstract_index,
)

CHECKPOINT_VERSION = 1
CAMPAIGN_THEME = "aureli_cider"
CAMPAIGN_QUERY = "cider"


class CampaignCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = CHECKPOINT_VERSION
    query: str = CAMPAIGN_QUERY
    profile: str
    target_candidates: int = Field(ge=1, le=40_000)
    start_year: int
    end_year: int
    next_year: int
    next_offset: int = Field(ge=0)
    raw_record_count: int = Field(ge=0)
    parsed_record_count: int = Field(ge=0)
    parse_error_count: int = Field(ge=0)
    run_id: str | None = None
    started_at: datetime
    backup_path: str | None = None
    backup_manifest_path: str | None = None
    baseline_statistics: dict[str, Any] | None = None
    finished: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--run-dir", type=Path, help="Persistent campaign directory")
    parser.add_argument("--limit", type=int, default=10_000)
    parser.add_argument("--start-year", type=int, default=datetime.now(UTC).year)
    parser.add_argument("--end-year", type=int, default=1600)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--request-delay", type=float, default=0.7)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect and classify without changing SQLite or Qdrant",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Persist accepted abstracts without updating the local vector index",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    _validate_arguments(args)

    settings = settings_for_corpus(load_settings(args.config), CorpusScope.COMMON)
    database = Database(settings.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_dir = _run_directory(settings.paths.exports_dir, args.run_dir, args.dry_run)
    run_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = run_dir / "checkpoint.json"
    page_log_path = run_dir / "pages.jsonl"
    screened_out_path = run_dir / "screened-out.jsonl"
    checkpoint = _load_or_create_checkpoint(args, checkpoint_path, run_dir)
    if checkpoint.finished:
        print(f"campaign=already_finished report={run_dir / 'report.json'}", flush=True)
        return 0

    active = settings.model_copy(deep=True)
    active.harvest.profile = checkpoint.profile
    active.harvest.per_source_limit = args.page_size
    active.bibliographic.request_delay_seconds = max(args.request_delay, 0.35)
    active.bibliographic.timeout_seconds = max(active.bibliographic.timeout_seconds, 30)
    active.bibliographic.max_retries = max(active.bibliographic.max_retries, 2)

    baseline = checkpoint.baseline_statistics or store.statistics()
    if not args.dry_run and checkpoint.run_id is None:
        checkpoint.baseline_statistics = baseline
        backup, manifest = _backup_database(database, settings.paths.data_dir)
        checkpoint.backup_path = str(backup)
        checkpoint.backup_manifest_path = str(manifest)
        checkpoint.run_id, checkpoint.started_at = store.start_run(
            active,
            themes={theme: CAMPAIGN_QUERY for theme in CIDER_PILOT_THEMES},
            sources=["Aureli"],
        )
        _write_checkpoint(checkpoint_path, checkpoint)

    errors: list[dict[str, str]] = []
    dry_counts = {"accepted": 0, "review": 0, "rejected": 0}
    dry_audit = run_dir / "dry-run-records.jsonl"
    try:
        with AureliClient(active) as client:
            _warmup_aureli_paging(client, checkpoint, args.page_size)
            while (
                checkpoint.raw_record_count < checkpoint.target_candidates
                and checkpoint.next_year >= checkpoint.end_year
            ):
                remaining = checkpoint.target_candidates - checkpoint.raw_record_count
                page_limit = min(args.page_size, remaining)
                page = client.search_articles(
                    checkpoint.query,
                    year=checkpoint.next_year,
                    limit=page_limit,
                    offset=checkpoint.next_offset,
                )
                page_hits: list[tuple[str, int, BibliographicRecord]] = []
                page_screened_out: list[dict[str, Any]] = []
                for rank, record in enumerate(page.records, start=1):
                    theme, assessment = assess_cider_relevance_across_themes(record)
                    decision = assessment.status
                    reason = assessment.reason
                    if decision == "accepted" and not record.abstract:
                        decision = "rejected"
                        reason = f"{reason}; abstract unavailable"
                    elif decision == "accepted" and not record.doi:
                        decision = "review"
                        reason = f"{reason}; verified DOI unavailable"
                    campaign_rank = checkpoint.raw_record_count + rank
                    if args.dry_run:
                        dry_counts[decision] += 1
                        _append_jsonl(
                            dry_audit,
                            {
                                "year_slice": page.year,
                                "rank": campaign_rank,
                                "source_id": record.source_id,
                                "title": record.title,
                                "doi": record.doi,
                                "abstract_available": bool(record.abstract),
                                "theme": theme,
                                "screening_decision": assessment.status,
                                "decision": decision,
                                "score": assessment.score,
                                "reason": reason,
                                "source_url": record.url,
                            },
                        )
                    elif decision == "rejected":
                        page_screened_out.append(
                            {
                                "rank": campaign_rank,
                                "record_id": None,
                                "source_id": record.source_id,
                                "title": record.title,
                                "doi": record.doi,
                                "publication_year": record.publication_year,
                                "theme": theme,
                                "decision": decision,
                                "score": assessment.score,
                                "reason": reason,
                                "content_level": (
                                    "Abstract only" if record.abstract else "Metadata only"
                                ),
                                "source_url": record.url,
                            }
                        )
                    else:
                        if checkpoint.run_id is None:
                            raise RuntimeError("Aureli campaign run is not initialized")
                        page_hits.append((theme, campaign_rank, record))
                if page_hits:
                    if checkpoint.run_id is None:
                        raise RuntimeError("Aureli campaign run is not initialized")
                    store.upsert_hits(run_id=checkpoint.run_id, hits=page_hits)
                if page_screened_out:
                    _append_jsonl_many(screened_out_path, page_screened_out)

                checkpoint.raw_record_count += page.raw_record_count
                checkpoint.parsed_record_count += len(page.records)
                checkpoint.parse_error_count += page.parse_error_count
                _append_jsonl(
                    page_log_path,
                    {
                        "collected_at": datetime.now(UTC).isoformat(),
                        "year": page.year,
                        "offset": page.offset,
                        "requested": page_limit,
                        "year_total": page.total_results,
                        "raw": page.raw_record_count,
                        "parsed": len(page.records),
                        "parse_errors": page.parse_error_count,
                        "campaign_raw_total": checkpoint.raw_record_count,
                    },
                )
                print(
                    f"year={page.year} offset={page.offset} "
                    f"raw={page.raw_record_count} parsed={len(page.records)} "
                    f"total={checkpoint.raw_record_count}/{checkpoint.target_candidates}",
                    flush=True,
                )
                if page.raw_record_count == 0 and checkpoint.next_offset < min(
                    page.total_results, aureli_max_offset() + 1
                ):
                    raise RuntimeError("Aureli returned an empty page before the year slice ended")
                _advance_checkpoint(checkpoint, page.total_results, page_limit)
                _write_checkpoint(checkpoint_path, checkpoint)
    except Exception as exc:
        errors.append(
            {
                "source": "Aureli",
                "error_type": type(exc).__name__,
                "message": str(exc)[:1000],
            }
        )
        _write_checkpoint(checkpoint_path, checkpoint)

    if args.dry_run:
        checkpoint.finished = not errors and (
            checkpoint.raw_record_count >= checkpoint.target_candidates
            or checkpoint.next_year < checkpoint.end_year
        )
        _write_checkpoint(checkpoint_path, checkpoint)
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "dry_run",
            "checkpoint": checkpoint.model_dump(mode="json"),
            "decisions": dry_counts,
            "errors": errors,
            "audit_path": str(dry_audit.resolve()),
        }
        _write_json(run_dir / "report.json", report)
        print(f"report={(run_dir / 'report.json').resolve()}", flush=True)
        return 2 if errors else 0

    if checkpoint.run_id is None:
        raise RuntimeError("Aureli campaign has no persistent run identifier")
    abstractless_rejected = 0
    doi_less_reviewed = 0
    if not errors:
        abstractless_rejected = store.reject_run_abstractless_records(checkpoint.run_id)
        doi_less_reviewed = store.review_run_doi_less_abstracts(checkpoint.run_id)
    completed_at = datetime.now(UTC)
    state: Literal["completed", "partial", "failed"] = "partial" if errors else "completed"
    unique_count, abstract_count, accepted_count, accepted_abstract_count = store.finish_run(
        run_id=checkpoint.run_id,
        state=state,
        raw_record_count=checkpoint.raw_record_count,
        errors=errors,
        completed_at=completed_at,
    )
    audit_path, decisions = _export_run_audit(
        database,
        checkpoint.run_id,
        run_dir,
        screened_out_path=screened_out_path,
    )
    cleanup = archive_and_purge_rejected_records(settings, database) if not errors else None

    index_payload: dict[str, Any] | None = None
    verification_payload: dict[str, Any] | None = None
    if not args.no_index and not errors and store.pending_abstracts(limit=1):
        backend = SentenceTransformerBackend(settings)
        index_report = index_bibliographic_abstracts(settings, store, backend)
        index_payload = index_report.model_dump(mode="json")
        verification = verify_bibliographic_abstract_index(settings, store)
        verification_payload = verification.model_dump(mode="json")

    checkpoint.finished = not errors
    _write_checkpoint(checkpoint_path, checkpoint)
    post_statistics = store.statistics()
    inaccessible_tail = _aureli_inaccessible_tail(page_log_path, args.page_size)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "apply",
        "query": checkpoint.query,
        "aureli_search": {
            "full_text_search": True,
            "document_type": "Article",
            "target_candidates": checkpoint.target_candidates,
            "raw_candidates": checkpoint.raw_record_count,
            "parsed_candidates": checkpoint.parsed_record_count,
            "parse_errors": checkpoint.parse_error_count,
            "year_range": [checkpoint.start_year, max(checkpoint.next_year, checkpoint.end_year)],
            "unavailable_beyond_year_cap": inaccessible_tail,
        },
        "run": {
            "run_id": checkpoint.run_id,
            "state": state,
            "unique_records": unique_count,
            "records_with_abstract": abstract_count,
            "accepted": accepted_count,
            "accepted_with_abstract": accepted_abstract_count,
            "abstractless_rejected": abstractless_rejected,
            "doi_less_reviewed": doi_less_reviewed,
            "decisions": decisions,
        },
        "content_levels": {
            "full_article_acquired_this_campaign": 0,
            "abstract_only": decisions.get("accepted_abstract_with_doi", 0),
            "metadata_only_review": decisions.get("review_metadata", 0),
            "abstract_review_without_doi": decisions.get("review_abstract_without_doi", 0),
        },
        "baseline_statistics": baseline,
        "final_statistics": post_statistics,
        "backup": {
            "path": checkpoint.backup_path,
            "manifest": checkpoint.backup_manifest_path,
        },
        "cleanup": cleanup.model_dump(mode="json") if cleanup is not None else None,
        "index": index_payload,
        "index_verification": verification_payload,
        "audit_path": str(audit_path.resolve()),
        "screened_out_path": str(screened_out_path.resolve()),
        "page_log_path": str(page_log_path.resolve()),
        "errors": errors,
    }
    _write_json(run_dir / "report.json", report)
    print(
        f"result=accepted:{accepted_count} review:{decisions.get('review', 0)} "
        f"rejected:{decisions.get('rejected', 0)} unique:{unique_count}",
        flush=True,
    )
    print(f"report={(run_dir / 'report.json').resolve()}", flush=True)
    return 2 if errors else 0


def _validate_arguments(args: argparse.Namespace) -> None:
    if not 1 <= args.limit <= 40_000:
        raise ValueError("Aureli candidate limit must be between 1 and 40000")
    current_year = datetime.now(UTC).year
    if not 1600 <= args.end_year <= args.start_year <= current_year:
        raise ValueError("Aureli year bounds are invalid")
    if not 1 <= args.page_size <= 50:
        raise ValueError("Aureli page size must be between 1 and 50")
    if not 0.35 <= args.request_delay <= 30:
        raise ValueError("Aureli request delay must be between 0.35 and 30 seconds")


def _run_directory(exports_dir: Path, requested: Path | None, dry_run: bool) -> Path:
    if requested is not None:
        return requested.resolve()
    suffix = "dry-run" if dry_run else "apply"
    return exports_dir / f"aureli-cider-{suffix}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"


def _load_or_create_checkpoint(
    args: argparse.Namespace,
    path: Path,
    run_dir: Path,
) -> CampaignCheckpoint:
    if path.is_file():
        checkpoint = CampaignCheckpoint.model_validate_json(path.read_bytes())
        if (
            checkpoint.target_candidates != args.limit
            or checkpoint.start_year != args.start_year
            or checkpoint.end_year != args.end_year
        ):
            raise ValueError("resume arguments do not match the Aureli checkpoint")
        if checkpoint.baseline_statistics is None:
            report_path = run_dir / "report.json"
            if report_path.is_file():
                prior_report = json.loads(report_path.read_text(encoding="utf-8"))
                prior_baseline = prior_report.get("baseline_statistics")
                if isinstance(prior_baseline, dict):
                    checkpoint.baseline_statistics = prior_baseline
        if checkpoint.next_offset > aureli_max_offset():
            checkpoint.next_year -= 1
            checkpoint.next_offset = 0
        _write_checkpoint(path, checkpoint)
        return checkpoint
    profile_stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    profile = f"aureli_cider_{profile_stamp}"
    checkpoint = CampaignCheckpoint(
        profile=profile,
        target_candidates=args.limit,
        start_year=args.start_year,
        end_year=args.end_year,
        next_year=args.start_year,
        next_offset=0,
        raw_record_count=0,
        parsed_record_count=0,
        parse_error_count=0,
        started_at=datetime.now(UTC),
    )
    _write_checkpoint(run_dir / "checkpoint.json", checkpoint)
    return checkpoint


def _advance_checkpoint(
    checkpoint: CampaignCheckpoint,
    year_total: int,
    requested: int,
) -> None:
    next_offset = checkpoint.next_offset + requested
    if next_offset >= year_total or next_offset > aureli_max_offset():
        checkpoint.next_year -= 1
        checkpoint.next_offset = 0
    else:
        checkpoint.next_offset = next_offset


def _warmup_aureli_paging(
    client: AureliClient,
    checkpoint: CampaignCheckpoint,
    page_size: int,
) -> None:
    """Replay prior pages so a resumed Primo session can safely reach a deep offset."""

    if checkpoint.next_offset == 0:
        return
    print(
        f"resume_warmup=starting year={checkpoint.next_year} "
        f"target_offset={checkpoint.next_offset}",
        flush=True,
    )
    for offset in range(0, checkpoint.next_offset, page_size):
        requested = min(page_size, checkpoint.next_offset - offset)
        page = client.search_articles(
            checkpoint.query,
            year=checkpoint.next_year,
            limit=requested,
            offset=offset,
        )
        if page.raw_record_count == 0 and offset < min(page.total_results, aureli_max_offset() + 1):
            raise RuntimeError("Aureli resume warm-up returned an unexpected empty page")
    print(
        f"resume_warmup=completed year={checkpoint.next_year} "
        f"target_offset={checkpoint.next_offset}",
        flush=True,
    )


def _backup_database(database: Database, data_dir: Path) -> tuple[Path, Path]:
    source_path = database.path.resolve()
    backup_dir = data_dir / "backups" / "aureli-cider"
    backup_dir.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(backup_dir).free
    required_bytes = source_path.stat().st_size + 512 * 1024 * 1024
    if free_bytes < required_bytes:
        raise RuntimeError("insufficient free disk space for the required SQLite backup")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"science_rag-before-aureli-{stamp}.sqlite3"
    temporary = target.with_suffix(".sqlite3.tmp")
    print(
        f"backup=starting source_bytes={source_path.stat().st_size} target={target}",
        flush=True,
    )
    try:
        with closing(database.connect()) as source, closing(sqlite3.connect(temporary)) as backup:
            source.backup(backup, pages=8192, sleep=0.05)
        with closing(sqlite3.connect(f"file:{temporary.as_posix()}?mode=ro", uri=True)) as check:
            result = str(check.execute("PRAGMA quick_check").fetchone()[0])
        if result != "ok":
            raise RuntimeError("SQLite backup quick_check failed")
        digest = _sha256_file(temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    manifest = target.with_suffix(".manifest.json")
    _write_json(
        manifest,
        {
            "created_at": datetime.now(UTC).isoformat(),
            "source": str(source_path),
            "backup": str(target.resolve()),
            "size": target.stat().st_size,
            "sha256": digest,
            "sqlite_quick_check": "ok",
        },
    )
    print(f"backup=verified sha256={digest}", flush=True)
    return target.resolve(), manifest.resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _export_run_audit(
    database: Database,
    run_id: str,
    run_dir: Path,
    *,
    screened_out_path: Path,
) -> tuple[Path, dict[str, int]]:
    destination = run_dir / "records-audit.jsonl"
    temporary = destination.with_suffix(".jsonl.tmp")
    decisions = {
        "accepted": 0,
        "review": 0,
        "rejected": 0,
        "accepted_abstract": 0,
        "accepted_abstract_with_doi": 0,
        "review_metadata": 0,
        "review_abstract_without_doi": 0,
    }
    active_record_ids: set[str] = set()
    active_source_ids: set[str] = set()
    with closing(database.connect()) as connection, temporary.open("w", encoding="utf-8") as out:
        rows = connection.execute(
            """
            SELECT h.rank, h.theme, h.relevance_status, h.relevance_score,
                h.relevance_reason, r.id, r.title, r.doi, r.publication_year,
                r.abstract, r.url, s.source_id
            FROM bibliographic_harvest_hits AS h
            JOIN bibliographic_records AS r ON r.id = h.record_id
            LEFT JOIN bibliographic_record_sources AS s
                ON s.record_id = r.id AND s.source = 'Aureli'
            WHERE h.run_id = ?
            ORDER BY h.rank, r.id
            """,
            (run_id,),
        )
        for row in rows:
            active_record_ids.add(str(row["id"]))
            if row["source_id"]:
                active_source_ids.add(str(row["source_id"]))
            status = str(row["relevance_status"])
            if status in {"accepted", "review", "rejected"}:
                decisions[status] += 1
            has_abstract = bool(row["abstract"])
            if status == "accepted" and has_abstract:
                decisions["accepted_abstract"] += 1
                if row["doi"]:
                    decisions["accepted_abstract_with_doi"] += 1
            if status == "review" and not has_abstract:
                decisions["review_metadata"] += 1
            if status == "review" and has_abstract and not row["doi"]:
                decisions["review_abstract_without_doi"] += 1
            out.write(
                json.dumps(
                    {
                        "rank": row["rank"],
                        "record_id": row["id"],
                        "source_id": row["source_id"],
                        "title": row["title"],
                        "doi": row["doi"],
                        "publication_year": row["publication_year"],
                        "theme": row["theme"],
                        "decision": status,
                        "score": row["relevance_score"],
                        "reason": row["relevance_reason"],
                        "content_level": "Abstract only" if has_abstract else "Metadata only",
                        "source_url": row["url"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        archived_rows = connection.execute(
            """
            SELECT a.original_record_id, a.doi, a.title, a.relevance_score,
                a.relevance_reason, a.relevance_theme, a.sources,
                a.first_archived_at, a.last_archived_at
            FROM rejected_bibliographic_archive AS a,
                json_each(a.harvest_run_ids) AS archived_run
            WHERE archived_run.value = ?
            ORDER BY a.first_archived_at, a.original_record_id
            """,
            (run_id,),
        )
        for row in archived_rows:
            record_id = str(row["original_record_id"])
            if record_id in active_record_ids:
                continue
            decisions["rejected"] += 1
            out.write(
                json.dumps(
                    {
                        "rank": None,
                        "record_id": record_id,
                        "source_id": None,
                        "title": row["title"],
                        "doi": row["doi"],
                        "publication_year": None,
                        "theme": row["relevance_theme"],
                        "decision": "rejected",
                        "score": row["relevance_score"],
                        "reason": row["relevance_reason"],
                        "content_level": "Archived metadata",
                        "source_url": None,
                        "sources": json.loads(str(row["sources"])),
                        "first_archived_at": row["first_archived_at"],
                        "last_archived_at": row["last_archived_at"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        if screened_out_path.is_file():
            screened_source_ids: set[str] = set()
            with screened_out_path.open(encoding="utf-8") as screened_records:
                for line in screened_records:
                    screened = json.loads(line)
                    source_id = str(screened.get("source_id") or "")
                    if (
                        not source_id
                        or source_id in active_source_ids
                        or source_id in screened_source_ids
                    ):
                        continue
                    screened_source_ids.add(source_id)
                    decisions["rejected"] += 1
                    out.write(json.dumps(screened, ensure_ascii=False) + "\n")
    temporary.replace(destination)
    return destination, decisions


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _append_jsonl_many(path: Path, payloads: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _aureli_inaccessible_tail(page_log_path: Path, page_size: int) -> dict[str, Any]:
    totals_by_year: dict[int, int] = {}
    if page_log_path.is_file():
        with page_log_path.open(encoding="utf-8") as pages:
            for line in pages:
                page = json.loads(line)
                year = int(page["year"])
                totals_by_year[year] = max(totals_by_year.get(year, 0), int(page["year_total"]))
    accessible_per_year = aureli_max_offset() + page_size
    by_year = {
        str(year): total - accessible_per_year
        for year, total in sorted(totals_by_year.items(), reverse=True)
        if total > accessible_per_year
    }
    return {
        "records": sum(by_year.values()),
        "by_year": by_year,
        "accessible_per_year": accessible_per_year,
    }


def _write_checkpoint(path: Path, checkpoint: CampaignCheckpoint) -> None:
    _write_json(path, checkpoint.model_dump(mode="json"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
