"""Strict local parser and source-title reconciliation for Scopus text exports."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.updates.harvest import bibliographic_content_hash
from app.updates.models import BibliographicRecord, clean_text, normalize_doi

_PUBLICATION_LINE = re.compile(r"^\((?P<year>\d{4})\)\s+(?P<body>.+)$")
_SCOPUS_URL = re.compile(r"^https://www\.scopus\.com/pages/publications/(?P<eid>[^?]+)")
_CITATION_COUNT = re.compile(r"^Cited (?P<count>\d+) times\.$")
_VOLUME_OR_ISSUE = re.compile(r"^(?:\d[\w./-]*(?:\s*\([^)]*\))?|\([^)]*\)|Part [A-Z]\d+)$")
_AUTHOR_ID = re.compile(r"\s*\(\d+\)\s*$")


class ScopusTextExportError(ValueError):
    """A local Scopus text export cannot be parsed without ambiguity."""


@dataclass(frozen=True, slots=True)
class ScopusCitation:
    publication_year: int
    source_title: str
    legacy_first_segment: str
    citation_count: int | None
    raw_line: str


@dataclass(frozen=True, slots=True)
class ScopusTextRecord:
    source_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str | None
    citation: ScopusCitation
    doi: str | None
    raw_doi: str | None
    document_type: str | None
    publication_stage: str | None
    open_access: str | None
    url: str
    input_name: str
    rank: int

    def to_bibliographic_record(self) -> BibliographicRecord:
        """Convert the local export record without inferring an organization publisher."""

        return BibliographicRecord(
            source="scopus",
            source_id=self.source_id,
            title=self.title,
            authors=list(self.authors),
            abstract=self.abstract,
            journal=self.citation.source_title,
            work_type=self.document_type,
            publisher=None,
            publication_year=self.citation.publication_year,
            doi=self.doi,
            citation_count=self.citation.citation_count,
            url=self.url,
        )


@dataclass(frozen=True, slots=True)
class SourceMetadataUpdate:
    record_id: str
    source_id: str
    expected_source_title: str | None
    source_title: str


@dataclass(frozen=True, slots=True)
class ArchivedSourceMetadataUpdate:
    original_record_id: str
    source_id: str
    source_title: str


@dataclass(frozen=True, slots=True)
class JournalUpdate:
    record_id: str
    expected_journal: str | None
    journal: str
    expected_content_hash: str
    content_hash: str
    reason: str


@dataclass(frozen=True, slots=True)
class ScopusSourceTitlePlan:
    input_record_count: int
    unique_source_id_count: int
    matched_source_rows: int
    unmatched_source_ids: tuple[str, ...]
    duplicate_input_observations: int
    source_metadata_updates: tuple[SourceMetadataUpdate, ...]
    archived_source_metadata_updates: tuple[ArchivedSourceMetadataUpdate, ...]
    journal_updates: tuple[JournalUpdate, ...]
    preserved_existing_journals: int
    record_source_title_conflicts: int
    input_source_conflicts: tuple[str, ...]
    database_source_conflicts: tuple[str, ...]
    source_metadata_conflicts: tuple[str, ...]
    database_snapshot_sha256: str

    def summary(self) -> dict[str, Any]:
        return {
            "input_records": self.input_record_count,
            "unique_scopus_eids": self.unique_source_id_count,
            "duplicate_input_observations": self.duplicate_input_observations,
            "matched_active_source_rows": self.matched_source_rows,
            "unmatched_source_eids": len(self.unmatched_source_ids),
            "per_source_titles_to_persist": len(self.source_metadata_updates),
            "archived_source_titles_to_persist": len(self.archived_source_metadata_updates),
            "scalar_journals_to_repair": len(self.journal_updates),
            "existing_journals_preserved": self.preserved_existing_journals,
            "records_with_multiple_source_titles": self.record_source_title_conflicts,
            "blocking_conflicts": (
                len(self.input_source_conflicts)
                + len(self.database_source_conflicts)
                + len(self.source_metadata_conflicts)
            ),
            "database_snapshot_sha256": self.database_snapshot_sha256,
        }


def parse_scopus_citation(line: str) -> ScopusCitation:
    """Parse the citation suffix from the right so commas remain in the source title."""

    match = _PUBLICATION_LINE.fullmatch(line.strip())
    if match is None:
        raise ScopusTextExportError(f"invalid Scopus publication line: {line[:200]}")
    body = match.group("body").strip()
    parts = body.split(", ")
    citation_count = None
    removed_suffix = False
    if parts and (citation_match := _CITATION_COUNT.fullmatch(parts[-1])):
        citation_count = int(citation_match.group("count"))
        parts.pop()
        removed_suffix = True
    while parts and parts[-1].startswith(("pp. ", "p. ", "art. no. ")):
        parts.pop()
        removed_suffix = True
    if parts and _VOLUME_OR_ISSUE.fullmatch(parts[-1]):
        parts.pop()
        removed_suffix = True
    if not parts:
        raise ScopusTextExportError(f"Scopus source title is empty: {line[:200]}")
    if not removed_suffix and ", " in body:
        raise ScopusTextExportError(
            f"ambiguous Scopus source title without volume/pages/citation suffix: {line[:200]}"
        )
    source_title = clean_text(", ".join(parts))
    if source_title is None:
        raise ScopusTextExportError(f"Scopus source title is empty: {line[:200]}")
    if source_title.startswith('"') and source_title.endswith('"'):
        source_title = source_title[1:-1].strip()
    return ScopusCitation(
        publication_year=int(match.group("year")),
        source_title=source_title,
        legacy_first_segment=body.split(",", 1)[0].strip(),
        citation_count=citation_count,
        raw_line=line.strip(),
    )


def parse_scopus_text_export(path: Path) -> list[ScopusTextRecord]:
    return parse_scopus_text(path.read_text(encoding="utf-8-sig"), input_name=path.name)


def parse_scopus_text(text: str, *, input_name: str = "<memory>") -> list[ScopusTextRecord]:
    lines = text.splitlines()
    source_markers = sum(line.strip() == "SOURCE: Scopus" for line in lines)
    if source_markers == 0:
        raise ScopusTextExportError(f"{input_name}: no Scopus records found")
    records: list[ScopusTextRecord] = []
    block: list[str] = []
    for line in lines:
        block.append(line)
        if line.strip() != "SOURCE: Scopus":
            continue
        records.append(_parse_record_block(block, input_name=input_name, rank=len(records) + 1))
        block = []
    if len(records) != source_markers:
        raise ScopusTextExportError(
            f"{input_name}: parsed {len(records)} records for {source_markers} markers"
        )
    return records


def _parse_record_block(block: list[str], *, input_name: str, rank: int) -> ScopusTextRecord:
    publication_indexes = [
        index for index, line in enumerate(block) if _PUBLICATION_LINE.fullmatch(line.strip())
    ]
    urls = [match for line in block if (match := _SCOPUS_URL.match(line.strip()))]
    if len(publication_indexes) != 1 or len(urls) != 1:
        raise ScopusTextExportError(
            f"{input_name} record {rank}: expected one publication line and one EID URL"
        )
    publication_index = publication_indexes[0]
    title = next(
        (cleaned for line in reversed(block[:publication_index]) if (cleaned := clean_text(line))),
        None,
    )
    if title is None or title.startswith(("AUTHOR FULL NAMES:", "EXPORT DATE:")):
        raise ScopusTextExportError(f"{input_name} record {rank}: document title is missing")
    citation = parse_scopus_citation(block[publication_index])
    raw_doi = _prefixed_value(block, "DOI:")
    url = urls[0].group(0)
    return ScopusTextRecord(
        source_id=urls[0].group("eid"),
        title=title,
        authors=_authors(block[:publication_index]),
        abstract=_prefixed_value(block, "ABSTRACT:"),
        citation=citation,
        doi=normalize_doi(raw_doi),
        raw_doi=raw_doi,
        document_type=_prefixed_value(block, "DOCUMENT TYPE:"),
        publication_stage=_prefixed_value(block, "PUBLICATION STAGE:"),
        open_access=_prefixed_value(block, "OPEN ACCESS:"),
        url=url,
        input_name=input_name,
        rank=rank,
    )


def _prefixed_value(lines: list[str], prefix: str) -> str | None:
    return next(
        (
            cleaned
            for line in lines
            if line.startswith(prefix) and (cleaned := clean_text(line[len(prefix) :]))
        ),
        None,
    )


def _authors(lines: list[str]) -> tuple[str, ...]:
    full_names = _prefixed_value(lines, "AUTHOR FULL NAMES:")
    if full_names:
        return tuple(
            name for item in full_names.split(";") if (name := _AUTHOR_ID.sub("", item).strip())
        )
    segment: list[str] = []
    for line in reversed(lines):
        if not line.strip() and segment:
            break
        if line.strip():
            segment.append(line.strip())
    author_line = segment[-1] if len(segment) >= 2 else ""
    return tuple(item.strip() for item in author_line.split(", ") if item.strip())


def plan_scopus_source_title_reconciliation(
    connection: sqlite3.Connection,
    records: list[ScopusTextRecord],
) -> ScopusSourceTitlePlan:
    """Plan conservative per-EID persistence and scalar journal repairs."""

    observations, input_conflicts = _unique_observations(records)
    has_source_title = "source_title" in {
        str(row[1]) for row in connection.execute("PRAGMA table_info(bibliographic_record_sources)")
    }
    source_title_sql = "s.source_title" if has_source_title else "NULL"
    rows = list(
        connection.execute(
            f"""
            SELECT s.record_id, s.source_id, {source_title_sql} AS persisted_source_title,
                b.*
            FROM bibliographic_record_sources AS s
            JOIN bibliographic_records AS b ON b.id = s.record_id
            WHERE s.source = 'scopus'
            ORDER BY s.source_id, s.record_id
            """
        )
    )
    rows_by_source: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        rows_by_source[str(row["source_id"])].append(row)
    database_conflicts = tuple(
        source_id
        for source_id, matches in rows_by_source.items()
        if source_id in observations and len(matches) > 1
    )
    metadata_updates: list[SourceMetadataUpdate] = []
    archived_metadata_updates: list[ArchivedSourceMetadataUpdate] = []
    metadata_conflicts: list[str] = []
    matched_rows: list[sqlite3.Row] = []
    observations_by_record: dict[str, list[ScopusTextRecord]] = defaultdict(list)
    for source_id, observation in observations.items():
        matches = rows_by_source.get(source_id, [])
        if len(matches) != 1:
            continue
        row = matches[0]
        matched_rows.append(row)
        observations_by_record[str(row["record_id"])].append(observation)
        persisted = row["persisted_source_title"]
        if persisted is None:
            metadata_updates.append(
                SourceMetadataUpdate(
                    record_id=str(row["record_id"]),
                    source_id=source_id,
                    expected_source_title=None,
                    source_title=observation.citation.source_title,
                )
            )
        elif _normalized(str(persisted)) != _normalized(observation.citation.source_title):
            metadata_conflicts.append(source_id)

    rows_by_record = {str(row["record_id"]): row for row in matched_rows}
    journal_updates: list[JournalUpdate] = []
    preserved = 0
    record_conflicts = 0
    for record_id, record_observations in sorted(observations_by_record.items()):
        distinct_titles = {
            _normalized(observation.citation.source_title) for observation in record_observations
        }
        if len(distinct_titles) > 1:
            record_conflicts += 1
        row = rows_by_record[record_id]
        current = str(row["journal"]).strip() if row["journal"] else None
        full_titles = [observation.citation.source_title for observation in record_observations]
        if current and any(_normalized(current) == _normalized(title) for title in full_titles):
            preserved += 1
            continue
        legacy_matches = [
            observation.citation.source_title
            for observation in record_observations
            if current
            and _normalized(current) == _normalized(observation.citation.legacy_first_segment)
            and _normalized(current) != _normalized(observation.citation.source_title)
        ]
        if legacy_matches:
            replacement = _preferred_title(legacy_matches)
            reason = "legacy_first_comma_truncation"
        elif current is None and len(distinct_titles) == 1:
            replacement = _preferred_title(full_titles)
            reason = "missing_scalar_journal"
        else:
            preserved += 1
            continue
        hash_values = _bibliographic_hash_values(row, journal=replacement)
        journal_updates.append(
            JournalUpdate(
                record_id=record_id,
                expected_journal=current,
                journal=replacement,
                expected_content_hash=str(row["content_hash"]),
                content_hash=bibliographic_content_hash(hash_values),
                reason=reason,
            )
        )

    unmatched_observations = {
        source_id: observation
        for source_id, observation in observations.items()
        if source_id not in rows_by_source
    }
    archived_by_doi: dict[str, str] = {}
    for row in connection.execute(
        """
        SELECT original_record_id, lower(trim(doi)) AS doi
        FROM rejected_bibliographic_archive
        WHERE doi IS NOT NULL AND trim(doi) != ''
        """
    ):
        archived_by_doi[str(row["doi"])] = str(row["original_record_id"])
    has_archived_sources = bool(
        connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'rejected_bibliographic_record_sources'
            """
        ).fetchone()
    )
    persisted_archived: dict[tuple[str, str], str | None] = {}
    if has_archived_sources:
        persisted_archived = {
            (str(row["original_record_id"]), str(row["source_id"])): (
                str(row["source_title"]) if row["source_title"] else None
            )
            for row in connection.execute(
                """
                SELECT original_record_id, source_id, source_title
                FROM rejected_bibliographic_record_sources
                WHERE source = 'scopus'
                """
            )
        }
    archived_matched_source_ids: set[str] = set()
    for source_id, observation in unmatched_observations.items():
        if observation.doi is None:
            continue
        original_record_id = archived_by_doi.get(observation.doi)
        if original_record_id is None:
            continue
        archived_matched_source_ids.add(source_id)
        persisted_title = persisted_archived.get((original_record_id, source_id))
        if persisted_title is None:
            archived_metadata_updates.append(
                ArchivedSourceMetadataUpdate(
                    original_record_id=original_record_id,
                    source_id=source_id,
                    source_title=observation.citation.source_title,
                )
            )
        elif _normalized(persisted_title) != _normalized(observation.citation.source_title):
            metadata_conflicts.append(source_id)

    unmatched = tuple(sorted(set(unmatched_observations).difference(archived_matched_source_ids)))
    snapshot = _snapshot_hash(matched_rows)
    return ScopusSourceTitlePlan(
        input_record_count=len(records),
        unique_source_id_count=len(observations),
        matched_source_rows=len(matched_rows),
        unmatched_source_ids=unmatched,
        duplicate_input_observations=len(records) - len(observations),
        source_metadata_updates=tuple(metadata_updates),
        archived_source_metadata_updates=tuple(archived_metadata_updates),
        journal_updates=tuple(journal_updates),
        preserved_existing_journals=preserved,
        record_source_title_conflicts=record_conflicts,
        input_source_conflicts=input_conflicts,
        database_source_conflicts=database_conflicts,
        source_metadata_conflicts=tuple(sorted(metadata_conflicts)),
        database_snapshot_sha256=snapshot,
    )


def apply_scopus_source_title_plan(
    connection: sqlite3.Connection,
    plan: ScopusSourceTitlePlan,
) -> dict[str, int]:
    blocking = (
        plan.input_source_conflicts
        + plan.database_source_conflicts
        + plan.source_metadata_conflicts
    )
    if blocking:
        raise RuntimeError(f"Scopus source-title reconciliation has {len(blocking)} conflicts")
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(bibliographic_record_sources)")
    }
    if "source_title" not in columns:
        raise RuntimeError("database migration for per-source titles has not been applied")
    for update in plan.source_metadata_updates:
        cursor = connection.execute(
            """
            UPDATE bibliographic_record_sources
            SET source_title = ?, last_seen_at = last_seen_at
            WHERE record_id = ? AND source = 'scopus' AND source_id = ?
              AND source_title IS ?
            """,
            (
                update.source_title,
                update.record_id,
                update.source_id,
                update.expected_source_title,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"Scopus source row changed during audit: {update.source_id}")
    for update in plan.archived_source_metadata_updates:
        connection.execute(
            """
            INSERT INTO rejected_bibliographic_record_sources (
                original_record_id, source, source_id, source_title
            ) VALUES (?, 'scopus', ?, ?)
            ON CONFLICT(original_record_id, source, source_id) DO UPDATE SET
                last_seen_at = CURRENT_TIMESTAMP,
                source_title = COALESCE(
                    rejected_bibliographic_record_sources.source_title,
                    excluded.source_title
                )
            """,
            (
                update.original_record_id,
                update.source_id,
                update.source_title,
            ),
        )
    for update in plan.journal_updates:
        cursor = connection.execute(
            """
            UPDATE bibliographic_records
            SET journal = ?, content_hash = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND journal IS ? AND content_hash = ?
            """,
            (
                update.journal,
                update.content_hash,
                update.record_id,
                update.expected_journal,
                update.expected_content_hash,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"bibliographic record changed during audit: {update.record_id}")
    return {
        "source_metadata_updated": len(plan.source_metadata_updates),
        "archived_source_metadata_updated": len(plan.archived_source_metadata_updates),
        "journals_updated": len(plan.journal_updates),
    }


def plan_updates_as_dicts(plan: ScopusSourceTitlePlan) -> dict[str, list[dict[str, Any]]]:
    return {
        "source_metadata_updates": [asdict(item) for item in plan.source_metadata_updates],
        "archived_source_metadata_updates": [
            asdict(item) for item in plan.archived_source_metadata_updates
        ],
        "journal_updates": [asdict(item) for item in plan.journal_updates],
    }


def _unique_observations(
    records: list[ScopusTextRecord],
) -> tuple[dict[str, ScopusTextRecord], tuple[str, ...]]:
    grouped: dict[str, list[ScopusTextRecord]] = defaultdict(list)
    for record in records:
        grouped[record.source_id].append(record)
    conflicts = tuple(
        sorted(
            source_id
            for source_id, items in grouped.items()
            if len({_normalized(item.citation.source_title) for item in items}) > 1
        )
    )
    observations = {
        source_id: max(
            items,
            key=lambda item: (len(item.citation.source_title), item.input_name, -item.rank),
        )
        for source_id, items in grouped.items()
    }
    return observations, conflicts


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _preferred_title(titles: list[str]) -> str:
    return max(dict.fromkeys(titles), key=lambda title: (len(title), title.casefold()))


def _bibliographic_hash_values(row: sqlite3.Row, *, journal: str) -> dict[str, Any]:
    return {
        "doi": row["doi"],
        "title": row["title"],
        "abstract": row["abstract"],
        "authors": row["authors"],
        "journal": journal,
        "work_type": row["work_type"],
        "publisher": row["publisher"],
        "publication_year": row["publication_year"],
        "citation_count": row["citation_count"],
        "url": row["url"],
    }


def _snapshot_hash(rows: list[sqlite3.Row]) -> str:
    payload = [
        {
            "record_id": row["record_id"],
            "source_id": row["source_id"],
            "source_title": row["persisted_source_title"],
            "journal": row["journal"],
            "content_hash": row["content_hash"],
        }
        for row in sorted(rows, key=lambda item: (item["source_id"], item["record_id"]))
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
