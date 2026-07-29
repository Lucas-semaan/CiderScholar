"""Run one durable, quota-aware cycle toward a target number of full-text PDFs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.admin.secrets import AdminBibliographicKeyVault
from app.config import load_settings
from app.corpora import CorpusScope, load_local_profile, settings_for_corpus
from app.database.sqlite import Database
from app.updates.full_text import FullTextHarvestService, FullTextStore
from app.updates.harvest import CIDER_BULK_QUERY_WAVES, CiderBulkHarvester
from app.updates.harvest_queries import (
    CIDER_EXPANDED_QUERY_WAVES,
    CIDER_MATERIAL_QUERY_WAVES,
    CIDER_SPECIALIZED_QUERY_WAVES,
)

GROWTH_NAME = "full_text_10000"
QUERY_SETS = (
    ("focused", CIDER_BULK_QUERY_WAVES),
    ("expanded", CIDER_EXPANDED_QUERY_WAVES),
    ("specialized", CIDER_SPECIALIZED_QUERY_WAVES),
    ("materials", CIDER_MATERIAL_QUERY_WAVES),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--target-pdfs", type=int, default=10_000)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--max-downloads", type=int, default=500)
    parser.add_argument("--skip-bibliography", action="store_true")
    parser.add_argument(
        "--slow-fallbacks",
        action="store_true",
        help="Also run one-DOI Unpaywall and Crossref fallbacks in this cycle",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if arguments.target_pdfs < 1:
        raise ValueError("target-pdfs must be positive")

    settings = load_settings(arguments.config)
    AdminBibliographicKeyVault(
        settings,
        load_local_profile(),
    ).hydrate_process_environment()
    settings.paths.create()
    database = Database(settings.paths.database_path)
    database.initialize()
    rag_settings = settings_for_corpus(settings, CorpusScope.COMMON)
    rag_database = Database(rag_settings.paths.database_path)
    rag_database.initialize()
    store = FullTextStore(database)

    state = _growth_state(database, arguments.target_pdfs)
    before = _full_pdf_count(rag_database)
    if before >= arguments.target_pdfs:
        _save_growth_state(
            database,
            target=arguments.target_pdfs,
            current=before,
            query_set_index=int(state["query_set_index"]),
            cycle_count=int(state["cycle_count"]),
            state="complete",
        )
        print(f"objectif atteint: {before}/{arguments.target_pdfs} PDF", flush=True)
        return 0

    query_index = int(state["query_set_index"]) % len(QUERY_SETS)
    query_name, query_waves = QUERY_SETS[query_index]
    bibliography: list[dict[str, Any]] = []
    if not arguments.skip_bibliography:
        for source in settings.bibliographic.sources:
            cooldown = store.active_cooldown(source)
            if cooldown is not None:
                bibliography.append(
                    {
                        "source": source,
                        "state": "deferred",
                        "retry_at": cooldown["retry_at"],
                        "reason": cooldown["reason"],
                    }
                )
                continue
            print(f"bibliographie {query_name}: {source}", flush=True)
            report = CiderBulkHarvester(settings, database).run(
                target_new_accepted_abstracts=10_000,
                page_size=arguments.page_size,
                max_runs=1,
                profile=f"cider_10k_{query_name}_{source}",
                query_waves=query_waves,
                sources=(source,),
                progress=lambda message: print(message, flush=True),
            )
            errors = [error for run in report.harvest_runs for error in run.errors]
            bibliography.append(
                {
                    "source": source,
                    "state": report.harvest_runs[-1].state if report.harvest_runs else "failed",
                    "new_accepted_abstracts": report.new_accepted_abstracts,
                    "errors": errors,
                }
            )
            retry_at = _retry_time_for_errors(errors)
            if retry_at is not None:
                store.set_cooldown(
                    source,
                    retry_at=retry_at,
                    reason=_error_summary(errors),
                )

    print("résolution et ingestion des textes intégraux", flush=True)
    audit, harvest = FullTextHarvestService(
        settings,
        database,
        rag_settings=rag_settings,
        rag_database=rag_database,
    ).run(
        include_slow_fallbacks=arguments.slow_fallbacks,
        max_downloads=arguments.max_downloads,
        progress=lambda message: print(message, flush=True),
    )
    after = _full_pdf_count(rag_database)
    next_retry_at = _next_retry_at(database)
    cycle_count = int(state["cycle_count"]) + 1
    state_name = "complete" if after >= arguments.target_pdfs else "active"
    if after == before and next_retry_at is not None:
        state_name = "waiting"

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "target_pdf_count": arguments.target_pdfs,
        "pdf_count_before": before,
        "pdf_count_after": after,
        "query_set": query_name,
        "bibliography": bibliography,
        "full_text": {
            "audited_dois": audit.doi_count,
            "resolved_dois": audit.resolved_count,
            "resolved_accepted_dois": audit.resolved_accepted_count,
            "available_by_source": audit.source_available_counts,
            "source_errors": audit.source_errors,
            "already_ingested": harvest.already_ingested,
            "downloaded": harvest.downloaded,
            "ingested": harvest.ingested,
            "duplicate": harvest.duplicate,
            "deferred": harvest.deferred,
            "failed": harvest.failed,
            "errors": harvest.errors,
        },
        "next_retry_at": next_retry_at,
        "state": state_name,
    }
    report_path = _write_report(settings.paths.exports_dir, payload)
    _save_growth_state(
        database,
        target=arguments.target_pdfs,
        current=after,
        query_set_index=(query_index + 1) % len(QUERY_SETS),
        cycle_count=cycle_count,
        state=state_name,
        next_retry_at=next_retry_at,
        report_path=str(report_path.resolve()),
    )
    print(
        f"progression={after}/{arguments.target_pdfs} nouveau={after - before} "
        f"état={state_name} prochain_retry={next_retry_at or '-'}",
        flush=True,
    )
    print(f"rapport={report_path.resolve()}", flush=True)
    return 0 if after > before or state_name == "complete" else 3


def _growth_state(database: Database, target: int) -> dict[str, Any]:
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO rag_growth_state (name, target_pdf_count)
            VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET
                target_pdf_count = excluded.target_pdf_count,
                updated_at = CURRENT_TIMESTAMP
            """,
            (GROWTH_NAME, target),
        )
        row = connection.execute(
            "SELECT * FROM rag_growth_state WHERE name = ?", (GROWTH_NAME,)
        ).fetchone()
    if row is None:
        raise RuntimeError("growth state could not be initialized")
    return dict(row)


def _save_growth_state(
    database: Database,
    *,
    target: int,
    current: int,
    query_set_index: int,
    cycle_count: int,
    state: str,
    next_retry_at: str | None = None,
    report_path: str | None = None,
    error: str | None = None,
) -> None:
    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE rag_growth_state
            SET target_pdf_count = ?, current_pdf_count = ?, query_set_index = ?,
                cycle_count = ?, state = ?, next_retry_at = ?,
                last_report_path = COALESCE(?, last_report_path),
                last_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE name = ?
            """,
            (
                target,
                current,
                query_set_index,
                cycle_count,
                state,
                next_retry_at,
                report_path,
                error,
                GROWTH_NAME,
            ),
        )


def _full_pdf_count(database: Database) -> int:
    with closing(database.connect()) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM articles AS a
            WHERE length(trim(a.pdf_path)) > 0
              AND EXISTS (SELECT 1 FROM chunks AS c WHERE c.article_id = a.id)
            """
        ).fetchone()
    return int(row[0])


def _retry_time_for_errors(errors: list[dict[str, str]]) -> datetime | None:
    if not errors:
        return None
    now = datetime.now(UTC)
    retry_times: list[datetime] = []
    for error in errors:
        message = str(error.get("message") or "")
        match = re.search(r"retry_at=([^;\s]+)", message)
        if match:
            try:
                retry_times.append(datetime.fromisoformat(match.group(1)).astimezone(UTC))
                continue
            except ValueError:
                pass
        folded = message.casefold()
        if "daily budget" in folded:
            tomorrow = (now + timedelta(days=1)).date()
            retry_times.append(datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC))
        elif "timeout" in folded or "timed out" in folded:
            retry_times.append(now + timedelta(hours=6))
        elif "429" in folded or "rate" in folded:
            retry_times.append(now + timedelta(hours=1))
        elif "quota" in folded or "weekly" in folded:
            retry_times.append(now + timedelta(days=7))
    return max(retry_times) if retry_times else None


def _error_summary(errors: list[dict[str, str]]) -> str:
    return " | ".join(str(error.get("message") or "") for error in errors)[:500]


def _next_retry_at(database: Database) -> str | None:
    with closing(database.connect()) as connection:
        row = connection.execute(
            """
            SELECT MIN(retry_at)
            FROM full_text_provider_cooldowns
            WHERE retry_at > strftime('%Y-%m-%d %H:%M:%S', 'now')
            """
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def _write_report(exports_dir: Path, payload: dict[str, Any]) -> Path:
    exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = exports_dir / f"full-text-growth-{stamp}.json"
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    raise SystemExit(main())
