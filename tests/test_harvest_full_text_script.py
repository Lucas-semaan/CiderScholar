from __future__ import annotations

import uuid

import pytest

from app.database.sqlite import Database
from app.updates.harvest import BibliographicHarvestStore
from app.updates.models import BibliographicRecord
from scripts.harvest_full_text import _record_ids_for_run, build_parser


def test_full_text_parser_accepts_bounded_cache_refresh() -> None:
    run_id = str(uuid.uuid4())

    arguments = build_parser().parse_args(
        [
            "--audit-only",
            "--refresh-cache",
            "--harvest-run-id",
            run_id,
            "--sources",
            "unpaywall",
            "crossref",
        ]
    )

    assert arguments.audit_only is True
    assert arguments.refresh_cache is True
    assert arguments.harvest_run_id == run_id
    assert arguments.sources == ["unpaywall", "crossref"]


def test_full_text_run_selection_returns_only_currently_accepted_records(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        settings,
        themes={"biochimie": "cider"},
        sources=["Aureli"],
    )
    record_id = store.upsert_hit(
        run_id=run_id,
        theme="biochimie",
        rank=1,
        record=BibliographicRecord(
            source="Aureli",
            source_id="accepted-source",
            title="Cider fermentation chemistry",
            abstract="Cider fermentation chemistry, acids, sugars, and polyphenols.",
            publication_year=2024,
            doi="10.1000/full-text-selection",
        ),
    )

    assert _record_ids_for_run(database, run_id) == [record_id]
    with pytest.raises(ValueError, match="UUID"):
        _record_ids_for_run(database, "not-a-uuid")
