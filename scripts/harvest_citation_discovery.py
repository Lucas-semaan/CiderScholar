"""Discover related DOI works through OpenCitations without writing to the corpus database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.admin.secrets import AdminBibliographicKeyVault
from app.config import load_settings
from app.corpora import CorpusScope, LocalProfile, load_local_profile, settings_for_corpus
from app.updates.base import BibliographicApiDeferred
from app.updates.harvest import assess_cider_relevance_across_themes
from app.updates.models import normalize_doi
from app.updates.opencitations import OpenCitationRelation, OpenCitationsClient

RELATIONS = ("citation", "reference")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--relations", nargs="+", choices=RELATIONS, default=list(RELATIONS))
    parser.add_argument("--max-seeds", type=int, default=1000)
    parser.add_argument("--max-candidates", type=int, default=40_000)
    parser.add_argument("--max-edges-per-seed", type=int, default=250)
    parser.add_argument("--metadata-batch-size", type=int, default=10)
    parser.add_argument("--timeout-hours", type=float, default=8.0)
    parser.add_argument("--deadline")
    parser.add_argument("--reset-deadline", action="store_true")
    parser.add_argument("--config", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    _validate_arguments(arguments)

    settings = settings_for_corpus(load_settings(arguments.config), CorpusScope.COMMON)
    profile = load_local_profile()
    if profile is LocalProfile.ADMIN:
        AdminBibliographicKeyVault(settings, profile).hydrate_process_environment()

    run_dir = arguments.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = run_dir / "checkpoint.json"
    jobs_path = run_dir / "jobs.jsonl"
    relations_path = run_dir / "candidate-relations.jsonl"
    decisions_path = run_dir / "candidate-decisions.jsonl"
    results_path = run_dir / "results.jsonl"
    report_path = run_dir / "report.json"

    checkpoint = _load_checkpoint(checkpoint_path)
    now = datetime.now(UTC)
    requested_deadline = _parse_deadline(arguments.deadline) if arguments.deadline else None
    deadline = _deadline(
        checkpoint,
        now,
        arguments.timeout_hours,
        requested=requested_deadline,
        reset=arguments.reset_deadline,
    )
    checkpoint.update(
        {
            "started_at": checkpoint.get("started_at") or now.isoformat(),
            "last_resumed_at": now.isoformat(),
            "deadline": deadline.isoformat(),
            "provider": "OpenCitations Index v2 and Meta v1",
        }
    )
    _write_json(checkpoint_path, checkpoint)

    seeds, existing_dois = _corpus_snapshot(
        settings.paths.database_path,
        arguments.max_seeds,
    )
    completed_jobs = _completed_jobs(jobs_path)
    relation_keys, pending = _load_relations(relations_path, existing_dois)
    decided = _decided_dois(decisions_path)
    pending = {doi: edges for doi, edges in pending.items() if doi not in decided}
    result_dois = _result_dois(results_path)
    deferred: BibliographicApiDeferred | None = None
    errors = 0

    with OpenCitationsClient(settings) as client:
        for seed_index, seed in enumerate(seeds, start=1):
            if (
                datetime.now(UTC) >= deadline
                or len(decided | set(pending)) >= arguments.max_candidates
            ):
                break
            for relation in dict.fromkeys(arguments.relations):
                if datetime.now(UTC) >= deadline:
                    break
                job_id = f"{seed['doi']}|{relation}"
                if job_id in completed_jobs:
                    continue
                try:
                    edges = client.relations(str(seed["doi"]), relation)
                except BibliographicApiDeferred as exc:
                    deferred = exc
                    _append_jsonl(jobs_path, _failed_job(job_id, seed, relation, exc, "deferred"))
                    break
                except Exception as exc:
                    errors += 1
                    _append_jsonl(jobs_path, _failed_job(job_id, seed, relation, exc, "error"))
                    continue

                kept = 0
                for edge in edges[: arguments.max_edges_per_seed]:
                    key = _relation_key(edge)
                    if key in relation_keys:
                        continue
                    relation_keys.add(key)
                    payload = {
                        **edge.model_dump(mode="json"),
                        "provider": "OpenCitations Index v2",
                        "seed_theme": seed["theme"],
                        "seed_title": seed["title"],
                        "discovered_at": datetime.now(UTC).isoformat(),
                    }
                    _append_jsonl(relations_path, payload)
                    kept += 1
                    if edge.related_doi not in existing_dois and edge.related_doi not in decided:
                        pending.setdefault(edge.related_doi, []).append(payload)
                    if len(decided | set(pending)) >= arguments.max_candidates:
                        break
                completed_jobs.add(job_id)
                _append_jsonl(
                    jobs_path,
                    {
                        "job_id": job_id,
                        "state": "completed",
                        "seed_doi": seed["doi"],
                        "seed_theme": seed["theme"],
                        "relation": relation,
                        "raw_edges": len(edges),
                        "new_edges": kept,
                        "observed_at": datetime.now(UTC).isoformat(),
                    },
                )
                print(
                    f"seed={seed_index}/{len(seeds)} relation={relation} "
                    f"raw={len(edges)} new_edges={kept} pending={len(pending)}",
                    flush=True,
                )
                if len(pending) >= arguments.metadata_batch_size:
                    try:
                        _resolve_pending(
                            client,
                            pending,
                            decided,
                            result_dois,
                            decisions_path,
                            results_path,
                            arguments.metadata_batch_size,
                        )
                    except BibliographicApiDeferred as exc:
                        deferred = exc
                        break
                    except Exception as exc:
                        errors += 1
                        print(f"metadata state=error type={type(exc).__name__}", flush=True)
            checkpoint.update(
                {
                    "state": "running",
                    "completed_jobs": len(completed_jobs),
                    "relation_edges": len(relation_keys),
                    "decided_candidates": len(decided),
                    "useful_results": len(result_dois),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            _write_json(checkpoint_path, checkpoint)
            if deferred is not None:
                break

        while pending and datetime.now(UTC) < deadline and deferred is None:
            try:
                _resolve_pending(
                    client,
                    pending,
                    decided,
                    result_dois,
                    decisions_path,
                    results_path,
                    arguments.metadata_batch_size,
                )
            except BibliographicApiDeferred as exc:
                deferred = exc
            except Exception as exc:
                errors += 1
                print(f"metadata state=error type={type(exc).__name__}", flush=True)
                break

    finished_at = datetime.now(UTC)
    total_jobs = len(seeds) * len(dict.fromkeys(arguments.relations))
    state = (
        "limit_reached"
        if len(decided | set(pending)) >= arguments.max_candidates
        else "timeout"
        if finished_at >= deadline
        else "deferred"
        if deferred is not None
        else "completed"
        if len(completed_jobs) >= total_jobs and not pending
        else "partial"
    )
    report = {
        "generated_at": finished_at.isoformat(),
        "state": state,
        "deadline": deadline.isoformat(),
        "seed_count": len(seeds),
        "total_jobs": total_jobs,
        "completed_jobs": len(completed_jobs),
        "relation_edges": len(relation_keys),
        "decided_candidates": len(decided),
        "pending_candidates": len(pending),
        "useful_results": len(result_dois),
        "errors": errors,
        "retry_at": deferred.retry_at.isoformat() if deferred is not None else None,
        "results_path": str(results_path),
        "relations_path": str(relations_path),
    }
    checkpoint.update(report)
    _write_json(checkpoint_path, checkpoint)
    _write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0


def _validate_arguments(arguments: argparse.Namespace) -> None:
    if not 1 <= arguments.max_seeds <= 10_000:
        raise ValueError("max-seeds must be between 1 and 10000")
    if not 1 <= arguments.max_candidates <= 40_000:
        raise ValueError("max-candidates must be between 1 and 40000")
    if not 1 <= arguments.max_edges_per_seed <= 1000:
        raise ValueError("max-edges-per-seed must be between 1 and 1000")
    if not 1 <= arguments.metadata_batch_size <= 50:
        raise ValueError("metadata-batch-size must be between 1 and 50")
    if not 0.1 <= arguments.timeout_hours <= 168:
        raise ValueError("timeout-hours must be between 0.1 and 168")


def _corpus_snapshot(database_path: Path, max_seeds: int) -> tuple[list[dict[str, Any]], set[str]]:
    uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        existing_dois = {
            doi
            for row in connection.execute(
                "SELECT doi FROM bibliographic_records WHERE doi IS NOT NULL"
            )
            if (doi := normalize_doi(row[0])) is not None
        }
        existing_dois.update(
            doi
            for row in connection.execute(
                "SELECT doi FROM rejected_bibliographic_archive WHERE doi IS NOT NULL"
            )
            if (doi := normalize_doi(row[0])) is not None
        )
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT doi, title, COALESCE(relevance_theme, '(none)') AS theme,
                    COALESCE(citation_count, 0) AS citation_count,
                    COALESCE(publication_year, 0) AS publication_year
                FROM bibliographic_records
                WHERE relevance_status = 'accepted' AND doi IS NOT NULL
                ORDER BY CASE WHEN COALESCE(citation_count, 0) <= 250 THEN 0 ELSE 1 END,
                    publication_year DESC, citation_count DESC, doi
                """
            )
        ]
    finally:
        connection.close()
    return _balanced_seeds(rows, max_seeds), existing_dois


def _balanced_seeds(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        doi = normalize_doi(row.get("doi"))
        if doi is None:
            continue
        groups[str(row.get("theme") or "(none)")].append({**row, "doi": doi})
    seeds: list[dict[str, Any]] = []
    positions = {theme: 0 for theme in groups}
    themes = sorted(groups)
    while len(seeds) < limit:
        progressed = False
        for theme in themes:
            position = positions[theme]
            if position >= len(groups[theme]):
                continue
            seeds.append(groups[theme][position])
            positions[theme] += 1
            progressed = True
            if len(seeds) >= limit:
                break
        if not progressed:
            break
    return seeds


def _resolve_pending(
    client: OpenCitationsClient,
    pending: dict[str, list[dict[str, Any]]],
    decided: set[str],
    result_dois: set[str],
    decisions_path: Path,
    results_path: Path,
    batch_size: int,
) -> None:
    dois = list(pending)[:batch_size]
    records = {record.doi: record for record in client.lookup_dois(dois) if record.doi}
    for doi in dois:
        relations = pending.pop(doi)
        record = records.get(doi)
        if record is None:
            decision = {
                "doi": doi,
                "status": "unresolved",
                "reason": "OpenCitations Meta did not return title metadata",
            }
        else:
            theme, assessment = assess_cider_relevance_across_themes(record)
            decision = {
                "doi": doi,
                "status": assessment.status,
                "score": assessment.score,
                "reason": assessment.reason,
                "theme": theme,
                "title": record.title,
            }
            if assessment.status in {"accepted", "review"} and doi not in result_dois:
                first = relations[0]
                _append_jsonl(
                    results_path,
                    {
                        "engine": "opencitations",
                        "theme": theme,
                        "url": f"https://doi.org/{doi}",
                        "title": record.title,
                        "doi": doi,
                        "seed_doi": first.get("seed_doi"),
                        "relation": first.get("relation"),
                        "citation_provider": "OpenCitations Index v2",
                        "relation_count": len(relations),
                        "preliminary_relevance_status": assessment.status,
                        "preliminary_relevance_score": assessment.score,
                        "preliminary_relevance_reason": assessment.reason,
                        "discovered_at": datetime.now(UTC).isoformat(),
                        "validation_state": "pending_exact_doi_validation",
                    },
                )
                result_dois.add(doi)
        decision.update(
            {
                "seed_doi": relations[0].get("seed_doi"),
                "relation": relations[0].get("relation"),
                "provider": "OpenCitations Index v2 and Meta v1",
                "relation_count": len(relations),
                "decided_at": datetime.now(UTC).isoformat(),
            }
        )
        _append_jsonl(decisions_path, decision)
        decided.add(doi)
    print(
        f"metadata resolved={len(records)}/{len(dois)} useful_total={len(result_dois)} "
        f"pending={len(pending)}",
        flush=True,
    )


def _load_relations(
    path: Path,
    existing_dois: set[str],
) -> tuple[set[str], dict[str, list[dict[str, Any]]]]:
    keys: set[str] = set()
    pending: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _jsonl(path):
        seed = normalize_doi(row.get("seed_doi"))
        related = normalize_doi(row.get("related_doi"))
        relation = str(row.get("relation") or "")
        if seed is None or related is None or relation not in RELATIONS:
            continue
        keys.add(f"{seed}|{relation}|{related}")
        if related not in existing_dois:
            pending[related].append(row)
    return keys, dict(pending)


def _relation_key(edge: OpenCitationRelation) -> str:
    return f"{edge.seed_doi}|{edge.relation}|{edge.related_doi}"


def _completed_jobs(path: Path) -> set[str]:
    return {
        str(row["job_id"])
        for row in _jsonl(path)
        if row.get("state") == "completed" and row.get("job_id")
    }


def _decided_dois(path: Path) -> set[str]:
    return {doi for row in _jsonl(path) if (doi := normalize_doi(row.get("doi"))) is not None}


def _result_dois(path: Path) -> set[str]:
    return {doi for row in _jsonl(path) if (doi := normalize_doi(row.get("doi"))) is not None}


def _failed_job(
    job_id: str,
    seed: dict[str, Any],
    relation: str,
    error: Exception,
    state: str,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "state": state,
        "seed_doi": seed["doi"],
        "seed_theme": seed["theme"],
        "relation": relation,
        "error_type": type(error).__name__,
        "message": str(error)[:500],
        "observed_at": datetime.now(UTC).isoformat(),
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


def _load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("citation discovery checkpoint is invalid")
    return payload


def _parse_deadline(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("deadline must include a timezone")
    return parsed.astimezone(UTC)


def _deadline(
    checkpoint: dict[str, Any],
    now: datetime,
    timeout_hours: float,
    *,
    requested: datetime | None,
    reset: bool,
) -> datetime:
    if requested is not None:
        return requested
    if not reset and checkpoint.get("deadline"):
        return _parse_deadline(str(checkpoint["deadline"]))
    return now + timedelta(hours=timeout_hours)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
