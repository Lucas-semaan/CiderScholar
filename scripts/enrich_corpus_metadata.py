"""Enrich existing corpus metadata with conservative, resumable API matching."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
from collections import defaultdict
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings, load_settings
from app.database.sqlite import Database
from app.services.bibliographic_metadata_enrichment import (
    MatchAssessment,
    MetadataTarget,
    MetadataUpdate,
    assess_cross_validated_candidate,
    assess_title_candidate,
    build_update,
    merge_provider_records,
    normalized_title,
    preferred_search_title,
    target_from_row,
)
from app.updates.base import BibliographicApiDeferred
from app.updates.clarivate import ClarivateClient
from app.updates.crossref import CrossrefClient
from app.updates.elsevier import ElsevierClient
from app.updates.europe_pmc import EuropePmcClient
from app.updates.models import BibliographicRecord, normalize_doi
from app.updates.openalex import OpenAlexClient

FIELDS = ("doi", "authors", "journal", "work_type", "publisher", "publication_year")
MAX_ELIGIBLE_YEAR = 2026
EXCLUDED_LOCAL_PATH_FRAGMENTS = ("\\biblio hg\\efficycle\\",)
DEFAULT_CURATION_AUDIT_GLOB = "exports/corpus-curation/local-non-article-audit-*/audit.csv"
MAX_DEFERRED_WAIT_SECONDS = 60.0


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        help="Resumable audit directory (default: a timestamped data/exports directory).",
    )
    parser.add_argument(
        "--curation-audit",
        type=Path,
        help="CSV whose metadata_action column controls lookup eligibility.",
    )
    parser.add_argument(
        "--title-limit",
        type=int,
        default=None,
        help="Optional safety cap for DOI-less article title searches.",
    )
    parser.add_argument(
        "--skip-title-search",
        action="store_true",
        help="Only resolve existing DOI and OpenAlex identifiers.",
    )
    parser.add_argument(
        "--fallback-sources",
        action="store_true",
        help="Use Europe PMC, Elsevier and Clarivate for unresolved scholarly-looking titles.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up SQLite and atomically apply accepted, non-conflicting updates.",
    )
    return parser.parse_args()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                values.append(value)
    return values


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _completed_queries(items: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {
        (str(item.get("record_id")), str(item.get("query")))
        for item in items
        if item.get("record_id") and item.get("query") and not item.get("error")
    }


def _search_with_short_deferred_retry(
    client: Any,
    query: str,
    limit: int,
) -> list[BibliographicRecord]:
    try:
        return client.search(query, limit)
    except BibliographicApiDeferred as exc:
        wait_seconds = max(0.0, (exc.retry_at - datetime.now(UTC)).total_seconds()) + 0.25
        if wait_seconds > MAX_DEFERRED_WAIT_SECONDS:
            raise
        time.sleep(wait_seconds)
        return client.search(query, limit)


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _field_expression(columns: set[str], field: str) -> str:
    return field if field in columns else f"NULL AS {field}"


def _load_targets(
    database_path: Path,
    curation_actions: dict[str, str] | None = None,
) -> tuple[list[MetadataTarget], dict[str, list[str]]]:
    actions = curation_actions or {}
    with closing(
        sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        connection.row_factory = sqlite3.Row
        article_columns = _columns(connection, "articles")
        record_columns = _columns(connection, "bibliographic_records")
        article_rows = connection.execute(
            f"""
            SELECT a.id, a.title, a.doi, a.authors, a.journal, a.publication_year,
                a.pdf_path, a.source,
                {_field_expression(article_columns, "work_type")},
                {_field_expression(article_columns, "publisher")}
            FROM articles AS a
            ORDER BY a.id
            """
        ).fetchall()
        original_paths: dict[str, str] = {}
        excluded_article_ids: set[str] = set()
        for row in connection.execute(
            """
            SELECT article_id, pdf_path
            FROM ingestion_jobs
            WHERE article_id IS NOT NULL
            ORDER BY created_at, id
            """
        ):
            article_id = str(row[0])
            path = str(row[1])
            original_paths.setdefault(article_id, path)
            if _excluded_local_path(path):
                excluded_article_ids.add(article_id)
        record_rows = connection.execute(
            f"""
            SELECT id, title, doi, authors, journal, publication_year,
                NULL AS pdf_path, NULL AS source,
                {_field_expression(record_columns, "work_type")},
                {_field_expression(record_columns, "publisher")}
            FROM bibliographic_records
            WHERE relevance_status = 'accepted'
              AND (publication_year IS NULL OR publication_year <= {MAX_ELIGIBLE_YEAR})
            ORDER BY id
            """
        ).fetchall()
        openalex_ids: dict[str, list[str]] = defaultdict(list)
        for row in connection.execute(
            """
            SELECT record_id, source_id
            FROM bibliographic_record_sources
            WHERE source = 'OpenAlex' AND source_id GLOB 'W[0-9]*'
            ORDER BY record_id, source_id
            """
        ):
            openalex_ids[str(row[0])].append(str(row[1]).upper())
    targets: list[MetadataTarget] = []
    for row in article_rows:
        record_id = str(row["id"])
        action = actions.get(record_id)
        if action == "skip_external_lookup":
            continue
        if record_id in excluded_article_ids or _excluded_local_path(row["pdf_path"]):
            continue
        values = dict(row)
        values["pdf_path"] = original_paths.get(record_id, values.get("pdf_path"))
        recorded_year = values.get("publication_year")
        if action == "validate_and_correct_year":
            values["publication_year"] = None
        elif isinstance(recorded_year, int) and recorded_year > MAX_ELIGIBLE_YEAR:
            continue
        targets.append(target_from_row("article", values))
    targets.extend(target_from_row("bibliographic_record", dict(row)) for row in record_rows)
    return targets, dict(openalex_ids)


def _excluded_local_path(value: object) -> bool:
    normalized = str(value or "").replace("/", "\\").casefold()
    return any(fragment in normalized for fragment in EXCLUDED_LOCAL_PATH_FRAGMENTS)


def _local_manifestation_type(target: MetadataTarget) -> str | None:
    path = normalized_title(Path(target.pdf_path or "").stem)
    title = normalized_title(target.title)
    padded_path = f" {path} "
    if " poster " in padded_path or title.startswith("poster "):
        return "conference-poster"
    if " ppt " in padded_path or title.startswith(
        ("presentation powerpoint", "powerpoint presentation", "slide ", "diapositive ")
    ):
        return "presentation"
    if any(
        token in f" {path} {title} "
        for token in (" supplementary ", " supporting information ", " supplemental ")
    ):
        return "supplementary-material"
    return None


def _load_curation_actions(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    if not path.is_file():
        raise FileNotFoundError(f"curation audit is unavailable: {path}")
    actions: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            record_id = str(row.get("article_id") or "").strip()
            action = str(row.get("metadata_action") or "").strip()
            if record_id and action:
                actions[record_id] = action
    return actions


def _web_validated_updates(
    targets: list[MetadataTarget],
    path: Path,
) -> list[MetadataUpdate]:
    targets_by_id = {target.record_id: target for target in targets}
    updates: list[MetadataUpdate] = []
    seen: set[str] = set()
    for item in _jsonl(path):
        record_id = str(item.get("record_id") or "").strip()
        target = targets_by_id.get(record_id)
        if target is None or record_id in seen:
            continue
        provider = str(item.get("provider") or "").strip()
        source_url = str(item.get("source_url") or "").strip()
        raw_fields = item.get("fields")
        if (
            not provider
            or not source_url.startswith("https://")
            or not isinstance(raw_fields, dict)
        ):
            raise ValueError(f"invalid web validation for record {record_id}")
        fields: dict[str, Any] = {}
        for field, value in raw_fields.items():
            if field not in FIELDS:
                continue
            if field == "authors" and target.authors:
                continue
            if field != "authors" and getattr(target, field) is not None:
                continue
            if field == "doi":
                normalized = normalize_doi(value)
                if normalized:
                    fields[field] = normalized
            elif field == "authors":
                if not isinstance(value, list):
                    raise ValueError(f"invalid web authors for record {record_id}")
                authors = [str(author).strip() for author in value if str(author).strip()]
                if authors:
                    fields[field] = list(dict.fromkeys(authors))
            elif field == "publication_year":
                try:
                    year = int(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid web year for record {record_id}") from exc
                if not 1600 <= year <= MAX_ELIGIBLE_YEAR:
                    raise ValueError(f"invalid web year for record {record_id}")
                fields[field] = year
            else:
                text = str(value or "").strip()
                if text:
                    fields[field] = text
        if fields:
            updates.append(
                MetadataUpdate(
                    kind=target.kind,
                    record_id=record_id,
                    provider=provider,
                    provider_id=str(item.get("provider_id") or source_url),
                    source_url=source_url,
                    method="manual_web_validation",
                    confidence=1.0,
                    fields=fields,
                )
            )
            seen.add(record_id)
    return updates


def _record_dict(record: BibliographicRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


def _record(value: dict[str, Any]) -> BibliographicRecord:
    return BibliographicRecord.model_validate(value)


def _openalex_doi_records(
    settings: Settings,
    targets: list[MetadataTarget],
    run_dir: Path,
) -> dict[str, BibliographicRecord]:
    path = run_dir / "openalex-doi.jsonl"
    cached = {
        str(item["query_doi"]): _record(item["record"])
        for item in _jsonl(path)
        if item.get("status") == "matched" and isinstance(item.get("record"), dict)
    }
    completed = {str(item["query_doi"]) for item in _jsonl(path) if item.get("query_doi")}
    dois = sorted({target.doi for target in targets if target.doi} - completed)
    if dois:
        with OpenAlexClient(settings) as client:
            budget = client.rate_limit_status()
            remaining = float(budget.get("daily_remaining_usd") or 0.0)
            projected = (len(dois) + 99) // 100 * 0.0001
            if settings.harvest.openalex_free_only and remaining < projected:
                raise RuntimeError("OpenAlex free daily budget is insufficient for DOI enrichment")
            for number, batch in enumerate(_chunks(dois, 100), start=1):
                results = {record.doi: record for record in client.lookup_dois(batch) if record.doi}
                for doi in batch:
                    record = results.get(doi)
                    item: dict[str, Any] = {
                        "query_doi": doi,
                        "status": "matched" if record else "missing",
                    }
                    if record:
                        item["record"] = _record_dict(record)
                        cached[doi] = record
                    _append_jsonl(path, item)
                print(
                    f"openalex-doi batch={number} resolved={len(results)}/{len(batch)}", flush=True
                )
    return cached


def _openalex_id_records(
    settings: Settings,
    identifiers_by_record: dict[str, list[str]],
    run_dir: Path,
) -> tuple[dict[str, BibliographicRecord], dict[str, str]]:
    path = run_dir / "openalex-id.jsonl"
    records_by_id: dict[str, BibliographicRecord] = {}
    completed: set[str] = set()
    for item in _jsonl(path):
        identifier = str(item.get("query_id") or "")
        if identifier:
            completed.add(identifier)
        if item.get("status") == "matched" and isinstance(item.get("record"), dict):
            records_by_id[identifier] = _record(item["record"])
    identifiers = sorted(
        {identifier for values in identifiers_by_record.values() for identifier in values}
        - completed
    )
    if identifiers:
        with OpenAlexClient(settings) as client:
            for number, batch in enumerate(_chunks(identifiers, 100), start=1):
                results = {record.source_id: record for record in client.lookup_ids(batch)}
                for identifier in batch:
                    record = results.get(identifier)
                    item: dict[str, Any] = {
                        "query_id": identifier,
                        "status": "matched" if record else "missing",
                    }
                    if record:
                        item["record"] = _record_dict(record)
                        records_by_id[identifier] = record
                    _append_jsonl(path, item)
                print(
                    f"openalex-id batch={number} resolved={len(results)}/{len(batch)}", flush=True
                )
    record_to_id = {
        record_id: next(
            (identifier for identifier in identifiers if identifier in records_by_id), ""
        )
        for record_id, identifiers in identifiers_by_record.items()
    }
    return records_by_id, record_to_id


def _local_title_candidates(
    targets: list[MetadataTarget],
) -> dict[str, BibliographicRecord]:
    by_title: dict[str, list[MetadataTarget]] = defaultdict(list)
    for target in targets:
        if target.kind == "bibliographic_record" and target.doi:
            by_title[normalized_title(target.title)].append(target)
    matches: dict[str, BibliographicRecord] = {}
    for target in targets:
        if target.kind != "article" or target.doi or _local_manifestation_type(target):
            continue
        candidates = by_title.get(normalized_title(target.title), [])
        if len(candidates) != 1:
            continue
        candidate = candidates[0]
        record = BibliographicRecord(
            source="Corpus bibliographique validé",
            source_id=candidate.record_id,
            title=candidate.title,
            authors=list(candidate.authors),
            journal=candidate.journal,
            work_type=candidate.work_type,
            publisher=candidate.publisher,
            publication_year=candidate.publication_year,
            doi=candidate.doi,
            url=f"https://doi.org/{candidate.doi}",
        )
        if assess_title_candidate(target, record).status == "accepted":
            matches[target.record_id] = record
    return matches


def _crossref_title_candidates(
    settings: Settings,
    targets: list[MetadataTarget],
    run_dir: Path,
    *,
    limit: int | None,
) -> dict[str, list[BibliographicRecord]]:
    path = run_dir / "crossref-title.jsonl"
    cached_items = _jsonl(path)
    completed = _completed_queries(cached_items)
    cached: dict[str, list[BibliographicRecord]] = {
        str(item["record_id"]): [
            _record(record) for record in item.get("records", []) if isinstance(record, dict)
        ]
        for item in cached_items
    }
    candidates = [
        target
        for target in targets
        if target.kind == "article"
        and target.doi is None
        and target.source != "IFPC"
        and _local_manifestation_type(target) is None
        and preferred_search_title(target)
        and (target.record_id, str(preferred_search_title(target))) not in completed
    ]
    if limit is not None:
        candidates = candidates[: max(0, limit)]
    if candidates:
        paced_settings = settings.model_copy(deep=True)
        paced_settings.bibliographic.request_delay_seconds = max(
            1.1,
            settings.bibliographic.request_delay_seconds,
        )
        with CrossrefClient(paced_settings) as client:
            for index, target in enumerate(candidates, start=1):
                query = preferred_search_title(target)
                assert query is not None
                deferred = False
                try:
                    records = _search_with_short_deferred_retry(client, query, 5)
                    error = None
                except Exception as exc:  # retained in the resumable audit
                    records = []
                    error = {"type": type(exc).__name__, "message": str(exc)[:500]}
                    deferred = isinstance(exc, BibliographicApiDeferred)
                cached[target.record_id] = records
                _append_jsonl(
                    path,
                    {
                        "record_id": target.record_id,
                        "query": query,
                        "records": [_record_dict(record) for record in records],
                        "error": error,
                    },
                )
                if index == 1 or index % 25 == 0 or index == len(candidates):
                    print(f"crossref-title progress={index}/{len(candidates)}", flush=True)
                if deferred:
                    print(
                        "crossref-title deferred; stopping until the provider retry window",
                        flush=True,
                    )
                    break
    return cached


def _best_crossref_candidate(
    target: MetadataTarget,
    candidates: list[BibliographicRecord],
) -> tuple[BibliographicRecord | None, MatchAssessment | None]:
    assessed = [(candidate, assess_title_candidate(target, candidate)) for candidate in candidates]
    eligible = [item for item in assessed if item[1].status != "rejected"]
    if not eligible:
        return None, max(
            (item[1] for item in assessed), key=lambda item: item.title_similarity, default=None
        )
    return max(eligible, key=lambda item: (item[1].status == "accepted", item[1].title_similarity))


def _needs_fallback_sources(
    target: MetadataTarget,
    crossref_records: list[BibliographicRecord],
) -> bool:
    candidate, assessment = _best_crossref_candidate(target, crossref_records)
    if candidate is not None and assessment is not None and assessment.status == "accepted":
        return False
    normalized_path = (target.pdf_path or "").replace("/", "\\").casefold()
    curated_local_path = "\\desktop\\biblio pascal\\" in normalized_path
    return curated_local_path or bool(assessment and assessment.title_similarity >= 0.8)


def _fallback_title_candidates(
    settings: Settings,
    targets: list[MetadataTarget],
    crossref_candidates: dict[str, list[BibliographicRecord]],
    run_dir: Path,
) -> dict[str, list[BibliographicRecord]]:
    path = run_dir / "fallback-title.jsonl"
    cached_items = _jsonl(path)
    records_by_target: dict[str, list[BibliographicRecord]] = defaultdict(list)
    completed: set[tuple[str, str]] = set()
    for item in cached_items:
        record_id = str(item.get("record_id") or "")
        source = str(item.get("source") or "")
        if record_id and source and not item.get("error"):
            completed.add((record_id, source))
        records_by_target[record_id].extend(
            _record(record) for record in item.get("records", []) if isinstance(record, dict)
        )
    candidates = [
        target
        for target in targets
        if target.kind == "article"
        and target.doi is None
        and _local_manifestation_type(target) is None
        and preferred_search_title(target)
        and _needs_fallback_sources(target, crossref_candidates.get(target.record_id, []))
    ]
    providers = (
        ("europe_pmc", EuropePmcClient),
        ("elsevier", ElsevierClient),
        ("clarivate", ClarivateClient),
    )
    total = sum(
        (target.record_id, source) not in completed
        for target in candidates
        for source, _ in providers
    )
    progress = 0
    for source, client_type in providers:
        with client_type(settings) as client:
            if not client.is_available():
                continue
            for target in candidates:
                key = (target.record_id, source)
                if key in completed:
                    continue
                query = preferred_search_title(target)
                assert query is not None
                deferred = False
                try:
                    records = _search_with_short_deferred_retry(client, query, 5)
                    error = None
                except Exception as exc:  # retained in the resumable audit
                    records = []
                    error = {"type": type(exc).__name__, "message": str(exc)[:500]}
                    deferred = isinstance(exc, BibliographicApiDeferred)
                records_by_target[target.record_id].extend(records)
                _append_jsonl(
                    path,
                    {
                        "record_id": target.record_id,
                        "source": source,
                        "query": query,
                        "records": [_record_dict(record) for record in records],
                        "error": error,
                    },
                )
                progress += 1
                if progress == 1 or progress % 25 == 0 or progress == total:
                    print(f"fallback-title progress={progress}/{total}", flush=True)
                if deferred:
                    print(
                        f"fallback-title source={source} deferred; stopping that provider",
                        flush=True,
                    )
                    break
    return dict(records_by_target)


def _candidate_doi_openalex(
    settings: Settings,
    candidates: dict[str, BibliographicRecord],
    run_dir: Path,
) -> dict[str, BibliographicRecord]:
    synthetic = [
        MetadataTarget(
            kind="article",
            record_id=record_id,
            title=record.title,
            doi=record.doi,
            authors=tuple(record.authors),
            journal=record.journal,
            work_type=record.work_type,
            publisher=record.publisher,
            publication_year=record.publication_year,
        )
        for record_id, record in candidates.items()
        if record.doi
    ]
    return _openalex_doi_records(settings, synthetic, run_dir / "candidate-validation")


def _build_updates(
    targets: list[MetadataTarget],
    openalex_by_doi: dict[str, BibliographicRecord],
    openalex_by_id: dict[str, BibliographicRecord],
    record_to_openalex_id: dict[str, str],
    local_candidates: dict[str, BibliographicRecord],
    crossref_candidates: dict[str, list[BibliographicRecord]],
    fallback_candidates: dict[str, list[BibliographicRecord]],
    settings: Settings,
    run_dir: Path,
) -> tuple[list[MetadataUpdate], list[dict[str, Any]]]:
    updates: list[MetadataUpdate] = []
    reviews: list[dict[str, Any]] = []
    targets_by_id = {target.record_id: target for target in targets}
    title_candidates: dict[str, BibliographicRecord] = dict(local_candidates)
    assessments: dict[str, MatchAssessment] = {}
    for record_id, records in crossref_candidates.items():
        target = targets_by_id.get(record_id)
        if target is None or record_id in title_candidates or _local_manifestation_type(target):
            continue
        candidate, assessment = _best_crossref_candidate(target, records)
        if assessment:
            assessments[record_id] = assessment
        if candidate and assessment and assessment.status == "accepted":
            title_candidates[record_id] = candidate
    for record_id, records in fallback_candidates.items():
        target = targets_by_id.get(record_id)
        if target is None or record_id in title_candidates or _local_manifestation_type(target):
            continue
        candidate, assessment = _best_crossref_candidate(target, records)
        if assessment:
            assessments[record_id] = assessment
        if candidate and assessment and assessment.status == "accepted":
            title_candidates[record_id] = candidate
    validation = _candidate_doi_openalex(settings, title_candidates, run_dir)
    for target in targets:
        if target.doi and target.doi in openalex_by_doi:
            update = build_update(
                target,
                openalex_by_doi[target.doi],
                method="openalex_exact_doi",
                confidence=1.0,
            )
            if update:
                updates.append(update)
            continue
        if target.kind == "bibliographic_record":
            identifier = record_to_openalex_id.get(target.record_id, "")
            if identifier and identifier in openalex_by_id:
                update = build_update(
                    target,
                    openalex_by_id[identifier],
                    method="openalex_exact_id",
                    confidence=1.0,
                )
                if update:
                    updates.append(update)
                continue
        if target.kind == "article" and target.source == "IFPC":
            fields: dict[str, Any] = {}
            if target.work_type is None:
                fields["work_type"] = "journal-article"
            if target.publisher is None:
                fields["publisher"] = "UNICID"
            if fields:
                updates.append(
                    MetadataUpdate(
                        kind="article",
                        record_id=target.record_id,
                        provider="IFPC",
                        provider_id=target.record_id,
                        source_url="https://www.ifpc.eu/cahiers-techniques/",
                        method="official_publisher_catalog",
                        confidence=1.0,
                        fields=fields,
                    )
                )
            continue
        manifestation_type = _local_manifestation_type(target)
        if target.kind == "article" and manifestation_type:
            if target.work_type is None:
                updates.append(
                    MetadataUpdate(
                        kind="article",
                        record_id=target.record_id,
                        provider="Corpus local",
                        provider_id=target.record_id,
                        source_url=None,
                        method="local_manifestation",
                        confidence=1.0,
                        fields={"work_type": manifestation_type},
                    )
                )
            continue
        candidate = title_candidates.get(target.record_id)
        if candidate is None or candidate.doi is None:
            assessment = assessments.get(target.record_id)
            if assessment and assessment.status == "review":
                reviews.append(
                    {
                        "record_id": target.record_id,
                        "title": target.title,
                        "reason": assessment.reason,
                        "similarity": assessment.title_similarity,
                    }
                )
            continue
        independent = validation.get(candidate.doi)
        assessment = assess_cross_validated_candidate(target, candidate, independent)
        if assessment.status != "accepted":
            reviews.append(
                {
                    "record_id": target.record_id,
                    "title": target.title,
                    "candidate_doi": candidate.doi,
                    "reason": assessment.reason,
                    "similarity": assessment.title_similarity,
                }
            )
            continue
        merged = merge_provider_records(candidate, independent)
        method = (
            "corpus_title_openalex_doi"
            if candidate.source == "Corpus bibliographique validé"
            else assessment.method
        )
        update = build_update(
            target,
            merged,
            method=method,
            confidence=assessment.title_similarity,
        )
        if update:
            updates.append(update)
    return updates, reviews


def _snapshot_database(settings: Settings) -> Path:
    directory = settings.paths.data_dir / "backups" / "manual"
    directory.mkdir(parents=True, exist_ok=True)
    target = (
        directory
        / f"science-rag-before-metadata-enrichment-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.sqlite3"
    )
    temporary = target.with_suffix(".tmp")
    try:
        with (
            closing(sqlite3.connect(settings.paths.common_database_path)) as source,
            closing(sqlite3.connect(temporary)) as destination,
        ):
            source.backup(destination)
            check = destination.execute("PRAGMA integrity_check").fetchone()
            if check is None or check[0] != "ok":
                raise RuntimeError("SQLite backup integrity check failed")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _existing_dois(connection: sqlite3.Connection, table: str) -> dict[str, str]:
    return {
        str(row[0]).casefold(): str(row[1])
        for row in connection.execute(
            f"SELECT doi, id FROM {table} WHERE doi IS NOT NULL AND trim(doi) != ''"
        )
    }


def _field_is_missing(field: str, value: object) -> bool:
    if value is None:
        return True
    if field == "authors":
        return str(value).strip().casefold() in {"", "[]", "null"}
    if field in {"doi", "journal", "work_type", "publisher"}:
        return not str(value).strip()
    return False


def _apply_updates(
    database: Database,
    updates: list[MetadataUpdate],
    *,
    replace_publication_year_ids: set[str] | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    database.initialize()
    replace_year_ids = replace_publication_year_ids or set()
    applied = 0
    conflicts: list[dict[str, Any]] = []
    with database.transaction() as connection:
        doi_maps = {
            "article": _existing_dois(connection, "articles"),
            "bibliographic_record": _existing_dois(connection, "bibliographic_records"),
        }
        for update in updates:
            table = "articles" if update.kind == "article" else "bibliographic_records"
            doi = update.fields.get("doi")
            if isinstance(doi, str):
                owner = doi_maps[update.kind].get(doi.casefold())
                if owner is not None and owner != update.record_id:
                    conflicts.append(
                        {
                            **update.as_dict(),
                            "reason": f"DOI already belongs to {update.kind} {owner}",
                        }
                    )
                    continue
            assignments: list[str] = []
            parameters: list[Any] = []
            current = connection.execute(
                f"SELECT {', '.join(FIELDS)} FROM {table} WHERE id = ?",
                (update.record_id,),
            ).fetchone()
            if current is None:
                conflicts.append({**update.as_dict(), "reason": "target record no longer exists"})
                continue
            for field, value in update.fields.items():
                if field not in FIELDS:
                    continue
                replace_validated_year = (
                    field == "publication_year" and update.record_id in replace_year_ids
                )
                if not replace_validated_year and not _field_is_missing(field, current[field]):
                    continue
                assignments.append(f"{field} = ?")
                parameters.append(
                    json.dumps(value, ensure_ascii=False) if field == "authors" else value
                )
            if not assignments:
                continue
            if table == "bibliographic_records":
                assignments.append("updated_at = CURRENT_TIMESTAMP")
            parameters.append(update.record_id)
            cursor = connection.execute(
                f"UPDATE {table} SET {', '.join(assignments)} WHERE id = ?", parameters
            )
            if cursor.rowcount:
                applied += 1
                if isinstance(doi, str):
                    doi_maps[update.kind][doi.casefold()] = update.record_id
    return applied, conflicts


def _missing_counts(database_path: Path) -> dict[str, dict[str, int]]:
    with closing(sqlite3.connect(database_path)) as connection:
        counts: dict[str, dict[str, int]] = {}
        for table, where in (
            ("articles", "1 = 1"),
            ("bibliographic_records", "relevance_status = 'accepted'"),
        ):
            columns = _columns(connection, table)
            values: dict[str, int] = {}
            for field in FIELDS:
                if field not in columns:
                    values[field] = int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE {where}"
                        ).fetchone()[0]
                    )
                    continue
                empty = f"{field} IS NULL"
                if field in {"doi", "journal", "work_type", "publisher"}:
                    empty += f" OR trim({field}) = ''"
                elif field == "authors":
                    empty += " OR trim(authors) IN ('', '[]')"
                values[field] = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE ({empty}) AND {where}"
                    ).fetchone()[0]
                )
            counts[table] = values
    return counts


def _excluded_future_year_counts(database_path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(database_path)) as connection:
        return {
            "articles": int(
                connection.execute(
                    "SELECT COUNT(*) FROM articles WHERE publication_year > ?",
                    (MAX_ELIGIBLE_YEAR,),
                ).fetchone()[0]
            ),
            "bibliographic_records": int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM bibliographic_records
                    WHERE relevance_status = 'accepted' AND publication_year > ?
                    """,
                    (MAX_ELIGIBLE_YEAR,),
                ).fetchone()[0]
            ),
        }


def _excluded_local_path_count(database_path: Path) -> int:
    with closing(sqlite3.connect(database_path)) as connection:
        excluded_ids = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT article_id, pdf_path
                FROM ingestion_jobs
                WHERE article_id IS NOT NULL
                """
            )
            if _excluded_local_path(row[1])
        }
        excluded_ids.update(
            str(row[0])
            for row in connection.execute("SELECT id, pdf_path FROM articles")
            if _excluded_local_path(row[1])
        )
        return len(excluded_ids)


def _assert_target_snapshot(
    initial_targets: list[MetadataTarget],
    current_targets: list[MetadataTarget],
    *,
    expected_count: int | None = None,
) -> None:
    initial_ids = {(target.kind, target.record_id) for target in initial_targets}
    current_ids = {(target.kind, target.record_id) for target in current_targets}
    if expected_count is not None and len(initial_targets) != expected_count:
        raise RuntimeError(
            "Corpus target count changed since the dry run: "
            f"expected {expected_count}, found {len(initial_targets)}"
        )
    if current_ids != initial_ids:
        added = sorted(current_ids - initial_ids)
        removed = sorted(initial_ids - current_ids)
        raise RuntimeError(
            "Corpus targets changed before the metadata transaction: "
            f"added={added[:10]}, removed={removed[:10]}"
        )


def main() -> int:
    args = _arguments()
    if args.title_limit is not None and args.title_limit < 0:
        raise ValueError("--title-limit cannot be negative")
    settings = load_settings()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (
        args.run_dir or settings.paths.data_dir / "exports" / "metadata-enrichment" / timestamp
    ).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    previous_report_path = run_dir / "report.json"
    previous_report: dict[str, Any] = {}
    if previous_report_path.is_file():
        loaded_report = json.loads(previous_report_path.read_text(encoding="utf-8"))
        if isinstance(loaded_report, dict):
            previous_report = loaded_report
    expected_target_count = None
    if args.apply and previous_report:
        value = previous_report.get("targets")
        if isinstance(value, int):
            expected_target_count = value
    default_audits = sorted(settings.paths.data_dir.glob(DEFAULT_CURATION_AUDIT_GLOB))
    audit_path = (
        args.curation_audit.resolve()
        if args.curation_audit
        else (default_audits[-1] if default_audits else None)
    )
    curation_actions = _load_curation_actions(audit_path)
    targets, identifiers_by_record = _load_targets(
        settings.paths.common_database_path,
        curation_actions,
    )
    baseline = _missing_counts(settings.paths.common_database_path)
    excluded_future_years = _excluded_future_year_counts(settings.paths.common_database_path)
    excluded_local_paths = _excluded_local_path_count(settings.paths.common_database_path)
    (run_dir / "baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    openalex_by_doi = _openalex_doi_records(settings, targets, run_dir)
    openalex_by_id, record_to_id = _openalex_id_records(settings, identifiers_by_record, run_dir)
    local_candidates = _local_title_candidates(targets)
    crossref_candidates = (
        {}
        if args.skip_title_search
        else _crossref_title_candidates(settings, targets, run_dir, limit=args.title_limit)
    )
    fallback_candidates = (
        _fallback_title_candidates(settings, targets, crossref_candidates, run_dir)
        if args.fallback_sources
        else {}
    )
    updates, reviews = _build_updates(
        targets,
        openalex_by_doi,
        openalex_by_id,
        record_to_id,
        local_candidates,
        crossref_candidates,
        fallback_candidates,
        settings,
        run_dir,
    )
    web_validation_path = run_dir / "web-validations.jsonl"
    web_updates = (
        _web_validated_updates(targets, web_validation_path)
        if web_validation_path.is_file()
        else []
    )
    updates.extend(web_updates)
    accepted_path = run_dir / "accepted-updates.jsonl"
    accepted_path.write_text(
        "".join(
            json.dumps(update.as_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            for update in updates
        ),
        encoding="utf-8",
    )
    (run_dir / "review-candidates.json").write_text(
        json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    backup: Path | None = None
    applied = 0
    conflicts: list[dict[str, Any]] = []
    target_snapshot_unchanged = not args.apply
    if args.apply:
        backup = _snapshot_database(settings)
        current_targets, _ = _load_targets(
            settings.paths.common_database_path,
            curation_actions,
        )
        _assert_target_snapshot(
            targets,
            current_targets,
            expected_count=expected_target_count,
        )
        target_snapshot_unchanged = True
        applied, conflicts = _apply_updates(
            Database(settings.paths.common_database_path),
            updates,
            replace_publication_year_ids={
                record_id
                for record_id, action in curation_actions.items()
                if action == "validate_and_correct_year"
            },
        )
    final_counts = _missing_counts(settings.paths.common_database_path)
    report = {
        "run_dir": str(run_dir),
        "database": str(settings.paths.common_database_path),
        "baseline": baseline,
        "final": final_counts,
        "targets": len(targets),
        "curation_audit": str(audit_path) if audit_path else None,
        "curation_skip_external_lookup": sum(
            action == "skip_external_lookup" for action in curation_actions.values()
        ),
        "curation_validate_and_correct_year": sum(
            action == "validate_and_correct_year" for action in curation_actions.values()
        ),
        "excluded_publication_year_after_2026": excluded_future_years,
        "excluded_local_path_records": excluded_local_paths,
        "openalex_doi_matches": len(openalex_by_doi),
        "openalex_id_matches": len(openalex_by_id),
        "local_title_candidates": len(local_candidates),
        "crossref_title_queries": len(crossref_candidates),
        "fallback_title_targets": len(fallback_candidates),
        "web_validated_updates": len(web_updates),
        "local_manifestation_updates": sum(
            update.method == "local_manifestation" for update in updates
        ),
        "accepted_updates": len(updates),
        "review_candidates": len(reviews),
        "applied_records": applied,
        "ignored_records": len(updates) - applied if args.apply else 0,
        "error_records": 0,
        "conflict_records": len(conflicts),
        "conflicts": conflicts,
        "target_snapshot_unchanged": target_snapshot_unchanged,
        "backup": str(backup) if backup else None,
        "applied": args.apply,
    }
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
