"""Manual decisions for bibliographic notices awaiting review."""

from __future__ import annotations

from typing import Any, Literal

from app.config import Settings
from app.database.sqlite import Database
from app.updates.harvest import BibliographicHarvestStore
from app.updates.vector_index import BibliographicVectorIndex


def decide_bibliographic_review(
    settings: Settings,
    database: Database,
    *,
    record_id: str,
    decision: Literal["accepted", "rejected"],
) -> dict[str, Any]:
    """Admit one review notice or remove it from every active storage layer."""

    store = BibliographicHarvestStore(database)
    review_record = store.review_record(record_id)
    if decision == "accepted":
        doi = review_record.get("doi")
        if isinstance(doi, str) and store.doi_exclusions.is_excluded(doi):
            store.doi_exclusions.reinstate(doi)
        return store.admit_review_record(record_id)

    store.doi_exclusions.exclude(
        review_record.get("doi"),
        title=str(review_record["title"]),
        reason="Rejet manuel depuis la file de révision.",
        origin="manual_review",
    )
    index = BibliographicVectorIndex(settings)
    try:
        vectors_deleted = index.delete([record_id])
    finally:
        index.close()
    result = store.delete_review_record(record_id)
    result["vectors_deleted"] = vectors_deleted
    return result
