"""Harvest authorized HTML search results for later DOI/title validation, without DB writes."""

from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.updates.base import BibliographicApiDeferred
from app.updates.harvest import (
    CIDER_BULK_QUERY_WAVES,
    assess_cider_relevance,
)
from app.updates.harvest_queries import (
    CIDER_EXPANDED_QUERY_WAVES,
    CIDER_MATERIAL_QUERY_WAVES,
    CIDER_MICROBIOLOGY_QUERY_WAVES,
    CIDER_SPECIALIZED_QUERY_WAVES,
)
from app.updates.models import BibliographicRecord
from app.updates.web_discovery import WebSearchClient

QUERY_SETS = {
    "focused": CIDER_BULK_QUERY_WAVES,
    "expanded": CIDER_EXPANDED_QUERY_WAVES,
    "specialized": CIDER_SPECIALIZED_QUERY_WAVES,
    "materials": CIDER_MATERIAL_QUERY_WAVES,
    "microbiology": CIDER_MICROBIOLOGY_QUERY_WAVES,
}
ENGINES = ("bing", "duckduckgo", "brave", "yahoo")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engines", nargs="+", choices=ENGINES, default=list(ENGINES))
    parser.add_argument(
        "--query-sets",
        nargs="+",
        choices=tuple(QUERY_SETS),
        default=list(QUERY_SETS),
    )
    parser.add_argument("--pages-per-query", type=int, default=3)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--max-results", type=int, default=40_000)
    parser.add_argument("--timeout-hours", type=float, default=3.0)
    parser.add_argument("--request-delay-seconds", type=float, default=2.0)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--reset-deadline", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not 1 <= arguments.pages_per_query <= 20:
        raise ValueError("pages-per-query must be between 1 and 20")
    if not 1 <= arguments.page_size <= 50:
        raise ValueError("page-size must be between 1 and 50")
    if not 1 <= arguments.max_results <= 40_000:
        raise ValueError("max-results must be between 1 and 40000")
    if not 0.1 <= arguments.timeout_hours <= 168:
        raise ValueError("timeout-hours must be between 0.1 and 168")
    if not 1.0 <= arguments.request_delay_seconds <= 60:
        raise ValueError("request delay must be between 1 and 60 seconds")

    run_dir = arguments.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"
    pages_path = run_dir / "pages.jsonl"
    checkpoint_path = run_dir / "checkpoint.json"
    checkpoint = _load_checkpoint(checkpoint_path)
    now = datetime.now(UTC)
    deadline = _deadline(
        checkpoint,
        now,
        arguments.timeout_hours,
        reset=arguments.reset_deadline,
    )
    checkpoint.setdefault("started_at", now.isoformat())
    checkpoint["last_resumed_at"] = now.isoformat()
    checkpoint["deadline"] = deadline.isoformat()
    checkpoint["authorization"] = "explicit_user_authorization_2026-08-13"
    _write_json(checkpoint_path, checkpoint)

    completed = _completed_jobs(pages_path)
    seen = _seen_results(results_path)
    jobs = _jobs(
        engines=tuple(dict.fromkeys(arguments.engines)),
        query_sets=tuple(dict.fromkeys(arguments.query_sets)),
        pages=arguments.pages_per_query,
    )
    blocked_until: dict[str, datetime] = {}
    clients = {
        engine: WebSearchClient(
            engine,
            request_delay_seconds=arguments.request_delay_seconds,
        )
        for engine in dict.fromkeys(arguments.engines)
    }
    added = 0
    doi_hits = 0
    try:
        while datetime.now(UTC) < deadline and len(seen) < arguments.max_results:
            progressed = False
            pending = False
            for job in jobs:
                if datetime.now(UTC) >= deadline or len(seen) >= arguments.max_results:
                    break
                job_id = _job_id(job)
                if job_id in completed:
                    continue
                pending = True
                engine = str(job["engine"])
                retry_at = blocked_until.get(engine)
                if retry_at is not None and datetime.now(UTC) < retry_at:
                    continue
                query = _web_query(str(job["query"]))
                try:
                    hits = clients[engine].search(
                        query,
                        page=int(job["page"]),
                        page_size=arguments.page_size,
                    )
                except BibliographicApiDeferred as exc:
                    blocked_until[engine] = exc.retry_at
                    _append_jsonl(
                        pages_path,
                        {
                            **job,
                            "job_id": job_id,
                            "state": "deferred",
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:500],
                            "retry_at": exc.retry_at.isoformat(),
                            "observed_at": datetime.now(UTC).isoformat(),
                        },
                    )
                    print(
                        f"engine={engine} state=deferred retry_at={exc.retry_at.isoformat()}",
                        flush=True,
                    )
                    continue
                except Exception as exc:
                    blocked_until[engine] = datetime.now(UTC) + timedelta(hours=6)
                    _append_jsonl(
                        pages_path,
                        {
                            **job,
                            "job_id": job_id,
                            "state": "error",
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:500],
                            "retry_at": blocked_until[engine].isoformat(),
                            "observed_at": datetime.now(UTC).isoformat(),
                        },
                    )
                    print(f"engine={engine} state=error type={type(exc).__name__}", flush=True)
                    continue

                new_on_page = 0
                page_dois = 0
                for rank, hit in enumerate(hits, start=1):
                    assessment = _preliminary_assessment(hit, str(job["theme"]))
                    if assessment is None:
                        continue
                    key = _result_key(hit.title, hit.url)
                    if key in seen:
                        continue
                    seen.add(key)
                    new_on_page += 1
                    page_dois += bool(hit.doi)
                    _append_jsonl(
                        results_path,
                        {
                            **job,
                            "job_id": job_id,
                            "rank": rank,
                            "title": hit.title,
                            "snippet": hit.snippet,
                            "url": hit.url,
                            "doi": hit.doi,
                            "preliminary_relevance_status": assessment.status,
                            "preliminary_relevance_score": assessment.score,
                            "preliminary_relevance_reason": assessment.reason,
                            "discovered_at": datetime.now(UTC).isoformat(),
                            "validation_state": "pending_api_or_institutional_validation",
                        },
                    )
                    if len(seen) >= arguments.max_results:
                        break
                completed.add(job_id)
                added += new_on_page
                doi_hits += page_dois
                progressed = True
                _append_jsonl(
                    pages_path,
                    {
                        **job,
                        "job_id": job_id,
                        "state": "completed",
                        "raw_hits": len(hits),
                        "new_hits": new_on_page,
                        "doi_hits": page_dois,
                        "observed_at": datetime.now(UTC).isoformat(),
                    },
                )
                checkpoint.update(
                    {
                        "state": "running",
                        "completed_jobs": len(completed),
                        "unique_results": len(seen),
                        "last_job_id": job_id,
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                )
                _write_json(checkpoint_path, checkpoint)
                print(
                    f"job={len(completed)}/{len(jobs)} engine={engine} "
                    f"query_set={job['query_set']} wave={job['wave']} theme={job['theme']} "
                    f"page={job['page']} raw={len(hits)} new={new_on_page} dois={page_dois} "
                    f"total={len(seen)}",
                    flush=True,
                )
            if not pending:
                break
            if progressed:
                continue
            future = [value for value in blocked_until.values() if value > datetime.now(UTC)]
            if not future:
                break
            retry_at = min(future)
            if retry_at >= deadline:
                break
            while datetime.now(UTC) < min(retry_at, deadline):
                remaining = (min(retry_at, deadline) - datetime.now(UTC)).total_seconds()
                time.sleep(max(0.1, min(60.0, remaining)))
    finally:
        for client in clients.values():
            client.close()

    finished_at = datetime.now(UTC)
    remaining_jobs = len(jobs) - len(completed)
    pending_retry = min(
        (value for value in blocked_until.values() if value > finished_at),
        default=None,
    )
    checkpoint.update(
        {
            "state": (
                "limit_reached"
                if len(seen) >= arguments.max_results
                else "timeout"
                if finished_at >= deadline
                else "deferred"
                if remaining_jobs and pending_retry is not None
                else "partial"
                if remaining_jobs
                else "completed"
            ),
            "completed_jobs": len(completed),
            "remaining_jobs": max(0, remaining_jobs),
            "unique_results": len(seen),
            "added_this_resume": added,
            "doi_hits_this_resume": doi_hits,
            "next_retry_at": pending_retry.isoformat() if pending_retry else None,
            "finished_at": finished_at.isoformat(),
        }
    )
    _write_json(checkpoint_path, checkpoint)
    print(json.dumps(checkpoint, ensure_ascii=False), flush=True)
    return 0


def _jobs(
    *,
    engines: tuple[str, ...],
    query_sets: tuple[str, ...],
    pages: int,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    max_waves = max(len(QUERY_SETS[name]) for name in query_sets)
    for page in range(pages):
        for wave_index in range(max_waves):
            for engine in engines:
                for query_set in query_sets:
                    waves = QUERY_SETS[query_set]
                    if wave_index >= len(waves):
                        continue
                    for theme, query in waves[wave_index].items():
                        jobs.append(
                            {
                                "engine": engine,
                                "query_set": query_set,
                                "wave": wave_index,
                                "theme": theme,
                                "page": page,
                                "query": query,
                            }
                        )
    return jobs


def _job_id(job: dict[str, Any]) -> str:
    return ":".join(str(job[field]) for field in ("engine", "query_set", "wave", "theme", "page"))


def _web_query(query: str) -> str:
    return (
        f"({query}) (apple OR pear OR fermentation OR juice OR pomace OR orchard OR Malus) "
        '("research article" OR study OR journal OR DOI) '
        "-fashion -clothing -shop -recipe -cocktail"
    )


def _preliminary_assessment(hit: Any, theme: str) -> Any | None:
    hostname = (urlsplit(hit.url).hostname or "").casefold()
    blocked_suffixes = (
        "shopcider.com",
        "cider.fr",
        "wikipedia.org",
        "youtube.com",
        "facebook.com",
        "reddit.com",
    )
    if any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in blocked_suffixes):
        return None
    record = BibliographicRecord(
        source=f"{hit.engine} web discovery",
        source_id=hit.url,
        title=hit.title,
        abstract=hit.snippet,
        doi=hit.doi,
        url=hit.url,
    )
    assessment = assess_cider_relevance(record, theme)
    if assessment.status == "rejected":
        return None
    scholarly_markers = (
        "doi.org",
        "journal",
        "article",
        "research",
        "study",
        ".pdf",
        "hal.science",
        "agris.fao.org",
        "pubmed",
        "europepmc",
        "sciencedirect",
        "springer",
        "wiley",
        "mdpi.com",
        "frontiersin.org",
        "tandfonline",
        "researchgate.net",
        "core.ac.uk",
        "zenodo.org",
    )
    combined = f"{hit.title} {hit.snippet or ''} {hit.url}".casefold()
    return (
        assessment if hit.doi or any(marker in combined for marker in scholarly_markers) else None
    )


def _result_key(title: str, url: str) -> str:
    return f"{title.casefold().strip()}|{url.casefold().rstrip('/')}"


def _completed_jobs(path: Path) -> set[str]:
    return {
        str(item["job_id"])
        for item in _jsonl(path)
        if item.get("state") == "completed" and item.get("job_id")
    }


def _seen_results(path: Path) -> set[str]:
    return {
        _result_key(str(item.get("title") or ""), str(item.get("url") or ""))
        for item in _jsonl(path)
        if item.get("title") and item.get("url")
    }


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        with suppress(json.JSONDecodeError):
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("web discovery checkpoint is invalid")
    return payload


def _deadline(
    checkpoint: dict[str, Any],
    now: datetime,
    timeout_hours: float,
    *,
    reset: bool,
) -> datetime:
    stored = checkpoint.get("deadline")
    if stored and not reset:
        with suppress(ValueError):
            parsed = datetime.fromisoformat(str(stored))
            if parsed.tzinfo is not None:
                return parsed.astimezone(UTC)
    if stored and reset:
        checkpoint.setdefault("deadline_history", []).append(
            {
                "deadline": stored,
                "resumed_at": now.isoformat(),
                "reason": "explicit_reset_deadline",
            }
        )
    return now + timedelta(hours=timeout_hours)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
