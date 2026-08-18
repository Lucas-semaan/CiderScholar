"""Harvest Semantic Scholar into a resumable staging area without writing SQLite."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from contextlib import closing, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.admin.secrets import AdminBibliographicKeyVault
from app.config import load_settings
from app.corpora import CorpusScope, LocalProfile, load_local_profile, settings_for_corpus
from app.updates.base import BibliographicApiDeferred
from app.updates.harvest import CIDER_BULK_QUERY_WAVES, assess_cider_relevance
from app.updates.harvest_queries import (
    CIDER_EXPANDED_QUERY_WAVES,
    CIDER_MATERIAL_QUERY_WAVES,
    CIDER_MICROBIOLOGY_QUERY_WAVES,
    CIDER_SPECIALIZED_QUERY_WAVES,
)
from app.updates.models import BibliographicRecord, normalize_doi
from app.updates.semantic_scholar import SemanticScholarClient
from app.updates.zenodo import ZenodoClient

QUERY_SETS = {
    "focused": CIDER_BULK_QUERY_WAVES,
    "expanded": CIDER_EXPANDED_QUERY_WAVES,
    "specialized": CIDER_SPECIALIZED_QUERY_WAVES,
    "materials": CIDER_MATERIAL_QUERY_WAVES,
    "microbiology": CIDER_MICROBIOLOGY_QUERY_WAVES,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=("semantic_scholar", "zenodo"),
        default="semantic_scholar",
        help="Official API used for the read-only staging harvest.",
    )
    parser.add_argument(
        "--query-sets",
        nargs="+",
        choices=tuple(QUERY_SETS),
        default=list(QUERY_SETS),
    )
    parser.add_argument("--pages-per-query", type=int, default=10)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-results", type=int, default=40_000)
    parser.add_argument("--timeout-hours", type=float, default=8.0)
    parser.add_argument("--deadline", type=datetime.fromisoformat)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not 1 <= arguments.pages_per_query <= 10:
        raise ValueError("pages-per-query must be between 1 and 10")
    maximum_page_size = 25 if arguments.provider == "zenodo" else 100
    if not 1 <= arguments.page_size <= maximum_page_size:
        raise ValueError(f"page-size must be between 1 and {maximum_page_size}")
    if not 1 <= arguments.max_results <= 40_000:
        raise ValueError("max-results must be between 1 and 40000")
    if not 0.1 <= arguments.timeout_hours <= 168:
        raise ValueError("timeout-hours must be between 0.1 and 168")

    settings = settings_for_corpus(load_settings(arguments.config), CorpusScope.COMMON)
    key_configured = False
    if arguments.provider == "semantic_scholar":
        profile = load_local_profile()
        if profile is not LocalProfile.ADMIN:
            raise PermissionError(
                "CIDERSCHOLAR_LOCAL_PROFILE=admin is required to hydrate the Semantic Scholar key"
            )
        AdminBibliographicKeyVault(settings, profile).hydrate_process_environment()
        key_name = settings.bibliographic.semantic_scholar_api_key_env
        key_configured = bool(os.environ.get(key_name, "").strip())
        if not key_configured:
            raise RuntimeError("Semantic Scholar API key is not configured")

    run_dir = arguments.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"
    pages_path = run_dir / "pages.jsonl"
    checkpoint_path = run_dir / "checkpoint.json"
    checkpoint = _load_json(checkpoint_path)
    started_at = datetime.now(UTC)
    deadline = _deadline(
        checkpoint=checkpoint,
        started_at=started_at,
        timeout_hours=arguments.timeout_hours,
        explicit=arguments.deadline,
    )
    checkpoint.update(
        {
            "version": 1,
            "provider": arguments.provider,
            "key_configured": key_configured,
            "started_at": checkpoint.get("started_at") or started_at.isoformat(),
            "last_resumed_at": started_at.isoformat(),
            "deadline": deadline.isoformat(),
            "database_access": "read_only_doi_snapshot",
        }
    )
    _write_json(checkpoint_path, checkpoint)

    initial_retry_at = _parse_retry_at(checkpoint.get("next_retry_at"))
    if (
        checkpoint.get("state") == "deferred"
        and initial_retry_at is not None
        and initial_retry_at > datetime.now(UTC)
    ):
        print(
            f"provider={arguments.provider} waiting_until={initial_retry_at.isoformat()}",
            flush=True,
        )
        while datetime.now(UTC) < min(initial_retry_at, deadline):
            remaining = (min(initial_retry_at, deadline) - datetime.now(UTC)).total_seconds()
            time.sleep(max(0.1, min(60.0, remaining)))

    completed = _completed_jobs(pages_path)
    seen = _seen_results(results_path)
    existing_dois = _existing_dois(settings.paths.database_path)
    jobs = _jobs(
        query_sets=tuple(dict.fromkeys(arguments.query_sets)),
        pages=arguments.pages_per_query,
    )
    active = settings.model_copy(deep=True)
    minimum_delay = 2.1 if arguments.provider == "zenodo" else 2.5
    active.bibliographic.request_delay_seconds = max(
        minimum_delay,
        settings.bibliographic.request_delay_seconds,
    )
    client_type = ZenodoClient if arguments.provider == "zenodo" else SemanticScholarClient
    added = 0
    duplicate_dois = 0
    retry_at: datetime | None = None
    with client_type(active) as client:
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
                try:
                    records = client.search(
                        str(job["query"]),
                        arguments.page_size,
                        offset=int(job["page"]) * arguments.page_size,
                    )
                except BibliographicApiDeferred as exc:
                    retry_at = exc.retry_at
                    _append_jsonl(
                        pages_path,
                        {
                            **job,
                            "job_id": job_id,
                            "state": "deferred",
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:500],
                            "retry_at": retry_at.isoformat(),
                            "observed_at": datetime.now(UTC).isoformat(),
                        },
                    )
                    _record_deferred_checkpoint(
                        checkpoint,
                        retry_at=retry_at,
                        completed_jobs=len(completed),
                        unique_results=len(seen),
                    )
                    _write_json(checkpoint_path, checkpoint)
                    print(
                        f"provider={arguments.provider} deferred_until={retry_at.isoformat()}",
                        flush=True,
                    )
                    break
                except Exception as exc:
                    _append_jsonl(
                        pages_path,
                        {
                            **job,
                            "job_id": job_id,
                            "state": "error",
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:500],
                            "observed_at": datetime.now(UTC).isoformat(),
                        },
                    )
                    print(
                        f"provider={arguments.provider} job={job_id} error={type(exc).__name__}",
                        flush=True,
                    )
                    continue

                new_on_page = 0
                existing_on_page = 0
                for rank, record in enumerate(records, start=1):
                    doi = normalize_doi(record.doi)
                    if doi and doi in existing_dois:
                        duplicate_dois += 1
                        existing_on_page += 1
                        continue
                    assessment = assess_cider_relevance(record, str(job["theme"]))
                    if assessment.status == "rejected":
                        continue
                    result_key = _result_key(record)
                    if result_key in seen:
                        continue
                    seen.add(result_key)
                    new_on_page += 1
                    added += 1
                    _append_jsonl(
                        results_path,
                        {
                            **job,
                            "engine": arguments.provider,
                            "job_id": job_id,
                            "rank": rank,
                            "title": record.title,
                            "snippet": record.abstract,
                            "url": record.url or (f"https://doi.org/{doi}" if doi else None),
                            "doi": doi,
                            "preliminary_relevance_status": assessment.status,
                            "preliminary_relevance_score": assessment.score,
                            "preliminary_relevance_reason": assessment.reason,
                            "provider_record": record.model_dump(mode="json"),
                            "discovered_at": datetime.now(UTC).isoformat(),
                            "validation_state": "pending_exact_api_validation",
                        },
                    )
                    if len(seen) >= arguments.max_results:
                        break
                completed.add(job_id)
                progressed = True
                _append_jsonl(
                    pages_path,
                    {
                        **job,
                        "job_id": job_id,
                        "state": "completed",
                        "raw_hits": len(records),
                        "existing_doi_hits": existing_on_page,
                        "new_hits": new_on_page,
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
                    f"job={len(completed)}/{len(jobs)} query_set={job['query_set']} "
                    f"wave={job['wave']} theme={job['theme']} page={job['page']} "
                    f"raw={len(records)} existing={existing_on_page} new={new_on_page} "
                    f"total={len(seen)}",
                    flush=True,
                )
            if not pending:
                break
            if retry_at is not None:
                if retry_at >= deadline:
                    break
                while datetime.now(UTC) < min(retry_at, deadline):
                    remaining = (min(retry_at, deadline) - datetime.now(UTC)).total_seconds()
                    time.sleep(max(0.1, min(60.0, remaining)))
                retry_at = None
                continue
            if not progressed:
                break

    finished_at = datetime.now(UTC)
    remaining_jobs = len(jobs) - len(completed)
    checkpoint.update(
        {
            "state": (
                "limit_reached"
                if len(seen) >= arguments.max_results
                else "timeout"
                if finished_at >= deadline
                else "deferred"
                if retry_at is not None
                else "partial"
                if remaining_jobs
                else "completed"
            ),
            "completed_jobs": len(completed),
            "remaining_jobs": max(0, remaining_jobs),
            "unique_results": len(seen),
            "added_this_resume": added,
            "existing_doi_hits_this_resume": duplicate_dois,
            "next_retry_at": retry_at.isoformat() if retry_at else None,
            "finished_at": finished_at.isoformat(),
        }
    )
    _write_json(checkpoint_path, checkpoint)
    print(json.dumps(checkpoint, ensure_ascii=False), flush=True)
    return 0


def _jobs(*, query_sets: tuple[str, ...], pages: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    max_waves = max(len(QUERY_SETS[name]) for name in query_sets)
    for page in range(pages):
        for wave_index in range(max_waves):
            for query_set in query_sets:
                waves = QUERY_SETS[query_set]
                if wave_index >= len(waves):
                    continue
                for theme, query in waves[wave_index].items():
                    jobs.append(
                        {
                            "query_set": query_set,
                            "wave": wave_index,
                            "theme": theme,
                            "page": page,
                            "query": query,
                        }
                    )
    return jobs


def _job_id(job: dict[str, Any]) -> str:
    return ":".join(str(job[field]) for field in ("query_set", "wave", "theme", "page"))


def _result_key(record: BibliographicRecord) -> str:
    doi = normalize_doi(record.doi)
    if doi:
        return f"doi:{doi}"
    if record.url:
        return f"url:{record.url.casefold().rstrip('/')}"
    return f"title:{record.title.casefold().strip()}"


def _existing_dois(database_path: Path) -> set[str]:
    if not database_path.is_file():
        return set()
    with closing(
        sqlite3.connect(f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True)
    ) as connection:
        return {
            normalized
            for row in connection.execute(
                "SELECT doi FROM bibliographic_records WHERE doi IS NOT NULL"
            )
            if (normalized := normalize_doi(row[0]))
        }


def _completed_jobs(path: Path) -> set[str]:
    return {
        str(row["job_id"])
        for row in _jsonl(path)
        if row.get("state") == "completed" and row.get("job_id")
    }


def _seen_results(path: Path) -> set[str]:
    seen: set[str] = set()
    for row in _jsonl(path):
        doi = normalize_doi(row.get("doi"))
        if doi:
            seen.add(f"doi:{doi}")
        elif row.get("url"):
            seen.add(f"url:{str(row['url']).casefold().rstrip('/')}")
        elif row.get("title"):
            seen.add(f"title:{str(row['title']).casefold().strip()}")
    return seen


def _deadline(
    *,
    checkpoint: dict[str, Any],
    started_at: datetime,
    timeout_hours: float,
    explicit: datetime | None,
) -> datetime:
    if explicit is not None:
        return explicit.astimezone(UTC)
    stored = checkpoint.get("deadline")
    if stored:
        with suppress(ValueError):
            return datetime.fromisoformat(str(stored)).astimezone(UTC)
    return started_at + timedelta(hours=timeout_hours)


def _parse_retry_at(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _record_deferred_checkpoint(
    checkpoint: dict[str, Any],
    *,
    retry_at: datetime,
    completed_jobs: int,
    unique_results: int,
) -> None:
    checkpoint.update(
        {
            "state": "deferred",
            "next_retry_at": retry_at.astimezone(UTC).isoformat(),
            "completed_jobs": completed_jobs,
            "unique_results": unique_results,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid JSON checkpoint: {path}")
    return value


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
