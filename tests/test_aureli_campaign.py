from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.database.sqlite import Database
from app.updates.harvest import BibliographicHarvestStore, RelevanceAssessment
from app.updates.models import BibliographicRecord
from scripts.harvest_aureli_cider import (
    CampaignCheckpoint,
    _advance_checkpoint,
    _aureli_inaccessible_tail,
    _campaign_exhausted,
    _campaign_record_decision,
    _current_journal,
    _export_run_audit,
    _warmup_aureli_paging,
)


class _RecordingClient:
    def __init__(self, *, empty_at: int | None = None) -> None:
        self.calls: list[tuple[str, int, int, int]] = []
        self.empty_at = empty_at

    def search_articles(
        self,
        query: str,
        *,
        year: int,
        limit: int,
        offset: int,
        journal: str | None = None,
        include_all_document_types: bool = False,
    ):
        del journal, include_all_document_types
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


def test_aureli_journal_campaign_advances_through_each_year_slice() -> None:
    checkpoint = _checkpoint(200)
    checkpoint.start_year = 2024
    checkpoint.end_year = 2023
    checkpoint.journal_titles = ["Journal A", "Journal B"]
    checkpoint.include_all_document_types = True

    _advance_checkpoint(checkpoint, year_total=220, requested=20)

    assert _current_journal(checkpoint) == "Journal A"
    assert checkpoint.next_year == 2023
    assert checkpoint.next_offset == 0
    assert not _campaign_exhausted(checkpoint)

    _advance_checkpoint(checkpoint, year_total=0, requested=50)

    assert _current_journal(checkpoint) == "Journal B"
    assert checkpoint.next_year == 2024
    assert checkpoint.next_offset == 0
    assert not _campaign_exhausted(checkpoint)

    checkpoint.next_year = 2023
    _advance_checkpoint(checkpoint, year_total=0, requested=50)

    assert _campaign_exhausted(checkpoint)


def test_aureli_full_text_campaign_retains_accepted_doi_without_abstract() -> None:
    record = BibliographicRecord(
        source="Aureli",
        source_id="scientific-cider-making",
        title="Scientific Cider Making",
        doi="10.1000/scientific-cider-making",
    )
    assessment = RelevanceAssessment(
        status="accepted",
        score=0.85,
        reason="explicit technical cider title",
    )

    decision, reason = _campaign_record_decision(
        record,
        assessment,
        retain_accepted_without_abstract=True,
    )

    assert decision == "accepted"
    assert "full-text acquisition" in reason


def test_aureli_default_campaign_still_rejects_abstractless_records() -> None:
    record = BibliographicRecord(
        source="Aureli",
        source_id="scientific-cider-making",
        title="Scientific Cider Making",
        doi="10.1000/scientific-cider-making",
    )
    assessment = RelevanceAssessment(
        status="accepted",
        score=0.85,
        reason="explicit technical cider title",
    )

    decision, reason = _campaign_record_decision(
        record,
        assessment,
        retain_accepted_without_abstract=False,
    )

    assert decision == "rejected"
    assert "abstract unavailable" in reason


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


def test_aureli_report_sums_inaccessible_tails_across_journals(tmp_path) -> None:
    page_log = tmp_path / "pages.jsonl"
    page_log.write_text(
        "\n".join(
            [
                json.dumps({"journal": "Journal A", "year": 2024, "year_total": 260}),
                json.dumps({"journal": "Journal B", "year": 2024, "year_total": 275}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert _aureli_inaccessible_tail(page_log, 50) == {
        "records": 35,
        "by_year": {"2024": 35},
        "accessible_per_year": 250,
        "by_journal_year": {
            "Journal A — 2024": 10,
            "Journal B — 2024": 25,
        },
    }
