"""Safe archival and removal of rejected bibliographic notices."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.database.sqlite import Database
from app.updates.harvest import BibliographicHarvestStore
from app.updates.vector_index import BibliographicVectorIndex


class RejectedCleanupReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archived_records: int = Field(ge=0)
    new_doi_exclusions: int = Field(ge=0)
    vectors_deleted: int = Field(ge=0)
    records_deleted: int = Field(ge=0)
    remaining_rejected_records: int = Field(ge=0)
    archive_total: int = Field(ge=0)
    export_path: str | None = None


def archive_and_purge_rejected_records(
    settings: Settings,
    database: Database,
    *,
    export: bool = True,
) -> RejectedCleanupReport:
    """Archive first, remove vector points second, then delete SQLite rows.

    If vector cleanup fails, the rejected SQLite records remain intact. Repeating the
    operation is safe because the archive upsert is keyed by the original record id.
    """

    store = BibliographicHarvestStore(database)
    archived = store.archive_rejected_records()
    record_ids = [str(record["original_record_id"]) for record in archived]
    new_doi_exclusions = store.exclude_archived_rejected_dois(record_ids)
    export_path = (
        _export_archive(settings.paths.exports_dir, archived) if export and archived else None
    )
    vectors_deleted = 0
    if record_ids:
        index = BibliographicVectorIndex(settings)
        try:
            vectors_deleted = index.delete(record_ids)
        finally:
            index.close()
    records_deleted = store.purge_archived_rejected_records(record_ids)
    statistics = store.archive_statistics()
    return RejectedCleanupReport(
        archived_records=len(archived),
        new_doi_exclusions=new_doi_exclusions,
        vectors_deleted=vectors_deleted,
        records_deleted=records_deleted,
        remaining_rejected_records=statistics["remaining_rejected_records"],
        archive_total=statistics["archive_total"],
        export_path=str(export_path.resolve()) if export_path is not None else None,
    )


def _export_archive(exports_dir: Path, records: list[dict[str, object]]) -> Path:
    exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = exports_dir / f"rejected-bibliographic-archive-{stamp}.json"
    temporary = destination.with_suffix(".json.tmp")
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "record_count": len(records),
        "records": records,
    }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination
