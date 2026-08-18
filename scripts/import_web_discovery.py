"""Validate HTML-search candidates through bibliographic APIs, then optionally persist them."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from contextlib import closing, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.admin.secrets import AdminBibliographicKeyVault
from app.config import load_settings
from app.corpora import CorpusScope, LocalProfile, load_local_profile, settings_for_corpus
from app.database.sqlite import Database
from app.services.bibliographic_metadata_enrichment import (
    MetadataTarget,
    assess_cross_validated_candidate,
    assess_title_candidate,
    normalized_title,
    title_similarity,
)
from app.updates.crossref import CrossrefClient
from app.updates.harvest import BibliographicHarvestStore
from app.updates.models import BibliographicRecord, normalize_doi
from app.updates.openalex import OpenAlexClient

TRUSTED_STAGED_API_ENGINES = {"semantic_scholar", "zenodo"}

_SEARCH_ENGINE_TITLE_SUFFIX = re.compile(
    r"\s*(?:\||[-–—])\s*(?:"
    r"ScienceDirect|Scientific Reports|SpringerLink|Wiley Online Library|Taylor & Francis|"
    r"PubMed|PubMed Central|MDPI|Frontiers|ResearchGate|HAL|Zenodo|CORE"
    r")(?:\s*\|.*)?\s*$",
    re.IGNORECASE,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-run-dir", type=Path, action="append", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--revalidate", action="store_true")
    parser.add_argument("--config", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not 1 <= arguments.limit <= 40_000:
        raise ValueError("validation limit must be between 1 and 40000")
    settings = settings_for_corpus(load_settings(arguments.config), CorpusScope.COMMON)
    profile = load_local_profile()
    if profile is LocalProfile.ADMIN:
        AdminBibliographicKeyVault(settings, profile).hydrate_process_environment()
    run_dir = arguments.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    validation_path = run_dir / "validation.jsonl"
    report_path = run_dir / "report.json"

    candidates = _load_candidates(arguments.input_run_dir)[: arguments.limit]
    completed = {
        str(row["candidate_key"]): row
        for row in _jsonl(validation_path)
        if row.get("candidate_key") and row.get("status") in {"accepted", "review", "rejected"}
    }
    pending = (
        candidates
        if arguments.revalidate
        else [row for row in candidates if str(row["candidate_key"]) not in completed]
    )
    if pending:
        active = settings.model_copy(deep=True)
        active.bibliographic.request_delay_seconds = max(
            1.0,
            settings.bibliographic.request_delay_seconds,
        )
        with CrossrefClient(active) as crossref:
            for index, candidate in enumerate(pending, start=1):
                result = _validate_candidate(candidate, crossref, active)
                _append_jsonl(validation_path, result)
                completed[str(candidate["candidate_key"])] = result
                if index == 1 or index % 25 == 0 or index == len(pending):
                    print(f"validation progress={index}/{len(pending)}", flush=True)

    validations = [completed[str(candidate["candidate_key"])] for candidate in candidates]
    counts: dict[str, int] = defaultdict(int)
    for validation in validations:
        counts[str(validation["status"])] += 1
    apply_report: dict[str, Any] | None = None
    if arguments.apply:
        database = Database(settings.paths.database_path)
        database.initialize()
        _require_no_running_harvest(database)
        apply_report = _apply_validated(settings, database, validations)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "input_run_dirs": [str(path.resolve()) for path in arguments.input_run_dir],
        "candidates": len(candidates),
        "validation_counts": dict(counts),
        "apply": apply_report,
        "validation_path": str(validation_path),
    }
    _write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0


def _validate_candidate(
    candidate: dict[str, Any],
    crossref: CrossrefClient,
    settings: Any,
) -> dict[str, Any]:
    base = {
        key: candidate.get(key)
        for key in (
            "candidate_key",
            "engine",
            "theme",
            "source_url",
            "title",
            "doi",
            "seed_doi",
            "relation",
            "citation_provider",
            "relation_count",
            "discovered_at",
        )
    }
    doi = normalize_doi(candidate.get("doi"))
    if doi:
        try:
            exact = crossref.lookup_dois([doi])
        except Exception as exc:
            return {
                **base,
                "status": "review",
                "method": "exact_doi_crossref",
                "reason": f"{type(exc).__name__}: {str(exc)[:400]}",
                "provider_record": None,
                "validated_at": datetime.now(UTC).isoformat(),
            }
        record = exact[0] if exact else _openalex_exact(settings, doi)
        if record is None:
            return {
                **base,
                "status": "review",
                "method": "exact_doi",
                "reason": "DOI not resolved by Crossref or OpenAlex",
                "provider_record": None,
                "validated_at": datetime.now(UTC).isoformat(),
            }
        similarity = title_similarity(candidate.get("title"), record.title)
        left = normalized_title(candidate.get("title"))
        right = normalized_title(record.title)
        aligned = similarity >= 0.65 or (left and right and (left in right or right in left))
        validated_record, enriched_engine = _validated_staged_provider(
            candidate,
            record,
        )
        return {
            **base,
            "status": "accepted" if aligned else "review",
            "method": (
                f"exact_doi_{record.source.casefold().replace(' ', '_')}"
                + (f"_{enriched_engine}_enriched" if enriched_engine else "")
            ),
            "reason": (
                f"exact DOI resolved and titles align ({similarity:.3f})"
                if aligned
                else f"exact DOI resolved but titles differ ({similarity:.3f})"
            ),
            "provider_record": validated_record.model_dump(mode="json"),
            "validated_at": datetime.now(UTC).isoformat(),
        }

    target = MetadataTarget(
        kind="bibliographic_record",
        record_id=str(candidate["candidate_key"]),
        title=str(candidate.get("title") or ""),
        doi=None,
        authors=(),
        journal=None,
        work_type=None,
        publisher=None,
        publication_year=None,
    )
    try:
        candidates = crossref.search(target.title, 5)
    except Exception as exc:
        return {
            **base,
            "status": "review",
            "method": "title_crossref_openalex",
            "reason": f"{type(exc).__name__}: {str(exc)[:400]}",
            "provider_record": None,
            "validated_at": datetime.now(UTC).isoformat(),
        }
    assessed = [(record, assess_title_candidate(target, record)) for record in candidates]
    accepted = [item for item in assessed if item[1].status == "accepted"]
    if not accepted:
        best = max(
            (item[1] for item in assessed), key=lambda item: item.title_similarity, default=None
        )
        return {
            **base,
            "status": "rejected",
            "method": "title_crossref_openalex",
            "reason": best.reason if best else "no Crossref title candidate",
            "provider_record": None,
            "validated_at": datetime.now(UTC).isoformat(),
        }
    record, _ = max(accepted, key=lambda item: item[1].title_similarity)
    openalex = _openalex_exact(settings, record.doi) if record.doi else None
    assessment = assess_cross_validated_candidate(target, record, openalex)
    return {
        **base,
        "status": assessment.status,
        "method": assessment.method,
        "reason": assessment.reason,
        "provider_record": record.model_dump(mode="json"),
        "secondary_record": openalex.model_dump(mode="json") if openalex else None,
        "validated_at": datetime.now(UTC).isoformat(),
    }


def _openalex_exact(settings: Any, doi: str) -> BibliographicRecord | None:
    try:
        with OpenAlexClient(settings) as client:
            records = client.lookup_dois([doi])
    except Exception:
        return None
    return next((record for record in records if record.doi == doi), None)


def _validated_staged_provider(
    candidate: dict[str, Any],
    identity_record: BibliographicRecord,
) -> tuple[BibliographicRecord, str | None]:
    engine = str(candidate.get("engine") or "")
    if engine not in TRUSTED_STAGED_API_ENGINES or not isinstance(
        candidate.get("provider_record"), dict
    ):
        return identity_record, None
    try:
        staged = BibliographicRecord.model_validate(candidate["provider_record"])
    except ValueError:
        return identity_record, None
    if not staged.doi or staged.doi != identity_record.doi:
        return identity_record, None
    similarity = title_similarity(staged.title, identity_record.title)
    if similarity < 0.8:
        return identity_record, None
    if len(staged.abstract or "") <= len(identity_record.abstract or ""):
        return identity_record, None
    return staged, engine


def _apply_validated(
    settings: Any,
    database: Database,
    validations: list[dict[str, Any]],
) -> dict[str, Any]:
    accepted = [
        row
        for row in validations
        if row.get("status") == "accepted" and isinstance(row.get("provider_record"), dict)
    ]
    if not accepted:
        return {"run_id": None, "raw_candidates": 0, "accepted_records": 0}
    active = settings.model_copy(deep=True)
    active.harvest.enabled = True
    active.harvest.profile = "web_discovery_validated_v1"
    store = BibliographicHarvestStore(database)
    themes = {
        str(row.get("theme") or "aromes_procede"): "authorized HTML discovery API validated"
        for row in accepted
    }
    sources = sorted({str(row.get("engine") or "web") for row in accepted})
    run_id, _ = store.start_run(active, themes=themes, sources=sources)
    ranks: dict[str, int] = defaultdict(int)
    for row in accepted:
        theme = str(row.get("theme") or "aromes_procede")
        ranks[theme] += 1
        provider = BibliographicRecord.model_validate(row["provider_record"])
        engine = str(row.get("engine") or "web")
        discovery_kind = "API" if engine in TRUSTED_STAGED_API_ENGINES else "web"
        record = provider.model_copy(
            update={
                "source": f"{engine} {discovery_kind} discovery validated by {provider.source}",
                "source_id": str(row.get("source_url") or provider.source_id),
            }
        )
        store.upsert_hit(run_id=run_id, theme=theme, rank=ranks[theme], record=record)
    completed_at = datetime.now(UTC)
    unique, abstracts, accepted_count, accepted_abstracts = store.finish_run(
        run_id=run_id,
        state="completed",
        raw_record_count=len(accepted),
        errors=[],
        completed_at=completed_at,
    )
    return {
        "run_id": run_id,
        "raw_candidates": len(accepted),
        "unique_records": unique,
        "abstract_records": abstracts,
        "accepted_records": accepted_count,
        "accepted_abstracts": accepted_abstracts,
    }


def _require_no_running_harvest(database: Database) -> None:
    with closing(database.connect()) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM bibliographic_harvest_runs WHERE state = 'running'"
        ).fetchone()
    if int(row[0] or 0):
        raise RuntimeError("web discovery import refuses to run while a harvest is active")


def _load_candidates(run_dirs: list[Path]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for run_dir in run_dirs:
        for row in _jsonl(run_dir.resolve() / "results.jsonl"):
            source_url = str(row.get("url") or "").strip()
            raw_title = str(row.get("title") or "").strip()
            title = _clean_web_title(raw_title)
            engine = str(row.get("engine") or "web")
            if not source_url.startswith("https://") or not title:
                continue
            candidate_key = f"{engine}|{source_url.casefold().rstrip('/')}"
            if candidate_key in seen:
                continue
            seen.add(candidate_key)
            candidates.append(
                {
                    "candidate_key": candidate_key,
                    "engine": engine,
                    "theme": row.get("theme"),
                    "source_url": source_url,
                    "title": title,
                    "raw_title": raw_title,
                    "doi": normalize_doi(row.get("doi")),
                    "seed_doi": normalize_doi(row.get("seed_doi")),
                    "relation": row.get("relation"),
                    "citation_provider": row.get("citation_provider"),
                    "relation_count": row.get("relation_count"),
                    "discovered_at": row.get("discovered_at"),
                    "provider_record": row.get("provider_record"),
                }
            )
    return candidates


def _clean_web_title(value: str) -> str:
    cleaned = " ".join(value.split())
    return _SEARCH_ENGINE_TITLE_SUFFIX.sub("", cleaned).strip() or cleaned


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
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
