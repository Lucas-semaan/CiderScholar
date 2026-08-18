from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.database.sqlite import Database
from app.updates.harvest import BibliographicHarvestStore
from app.updates.models import BibliographicRecord
from scripts.harvest_aureli_cider import (
    CampaignCheckpoint,
    _aureli_inaccessible_tail,
    _export_run_audit,
    _warmup_aureli_paging,
)


class _RecordingClient:
    def __init__(self, *, empty_at: int | None = None) -> None:
        self.calls: list[tuple[str, int, int, int]] = []
        self.empty_at = empty_at

    def search_articles(self, query: str, *, year: int, limit: int, offset: int):
        self.calls.append((query, year, limit, offset))
        raw = 0 if offset == self.empty_at else limit
        return SimpleNamespace(raw_record_count=raw, total_results=1000)


def _checkpoint(offset: int) -> CampaignCheckpoint:
    return CampaignCheckpoint(
        profile="test",
        target_candidates=1000,
        start_year=2026,
        end_year=1900,
        next_year=2024,
        next_offset=offset,
        raw_record_count=100,
        parsed_record_count=100,
        parse_error_count=0,
        started_at=datetime.now(UTC),
    )


def test_aureli_resume_replays_pages_before_deep_offset() -> None:
    client = _RecordingClient()

    _warmup_aureli_paging(client, _checkpoint(120), 50)  # type: ignore[arg-type]

    assert client.calls == [
        ("cider", 2024, 50, 0),
        ("cider", 2024, 50, 50),
        ("cider", 2024, 20, 100),
    ]


def test_aureli_resume_rejects_an_unexpected_empty_warmup_page() -> None:
    client = _RecordingClient(empty_at=50)

    with pytest.raises(RuntimeError, match="warm-up"):
        _warmup_aureli_paging(client, _checkpoint(100), 50)  # type: ignore[arg-type]


def test_aureli_audit_merges_screened_records_without_source_duplicates(settings, tmp_path) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        settings,
        themes={"biochimie": "cider"},
        sources=["Aureli"],
    )
    store.upsert_hit(
        run_id=run_id,
        theme="biochimie",
        rank=1,
        record=BibliographicRecord(
            source="Aureli",
            source_id="active-source",
            title="Chemical characterization of cider fermentation",
            abstract="Cider fermentation chemistry, acids, sugars, and polyphenols.",
            publication_year=2024,
            doi="10.1000/active-cider",
        ),
    )
    screened_path = tmp_path / "screened-out.jsonl"
    screened_path.write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {"source_id": "active-source", "decision": "rejected"},
                {
                    "source_id": "screened-source",
                    "title": "CIDER software acronym",
                    "decision": "rejected",
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    audit_path, decisions = _export_run_audit(
        database,
        run_id,
        tmp_path,
        screened_out_path=screened_path,
    )

    audit = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert decisions["accepted"] == 1
    assert decisions["rejected"] == 1
    assert {record["source_id"] for record in audit} == {
        "active-source",
        "screened-source",
    }


def test_aureli_report_quantifies_the_authenticated_year_tail(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CIDERSCHOLAR_AURELI_SESSION_TOKEN", "campaign-token")
    page_log = tmp_path / "pages.jsonl"
    page_log.write_text(
        "\n".join(
            [
                json.dumps({"year": 2022, "year_total": 2018}),
                json.dumps({"year": 2022, "year_total": 2018}),
                json.dumps({"year": 2021, "year_total": 1700}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert _aureli_inaccessible_tail(page_log, 50) == {
        "records": 18,
        "by_year": {"2022": 18},
        "accessible_per_year": 2000,
    }
