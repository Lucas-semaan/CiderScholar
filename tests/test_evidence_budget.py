from __future__ import annotations

from app.retrieval.evidence_budget import select_records_with_axis_coverage


def test_budget_reserves_one_distinct_record_for_every_documented_axis() -> None:
    records = [f"record-{index}" for index in range(12)]

    selected = select_records_with_axis_coverage(
        records,
        axis_candidate_ids={
            "primary_axis": ["record-0"],
            "required_axis_b": ["record-10", "record-0"],
            "required_axis_c": ["record-11", "record-0"],
        },
        record_id=lambda record: record,
        record_limit=10,
    )

    assert selected[:3] == ["record-0", "record-10", "record-11"]
    assert "record-1" in selected
    assert "record-9" not in selected


def test_budget_reassigns_a_shared_record_to_avoid_losing_an_axis() -> None:
    selected = select_records_with_axis_coverage(
        ["shared", "first-only", "second-only"],
        axis_candidate_ids={
            "first_axis": ["shared", "first-only"],
            "second_axis": ["shared", "second-only"],
        },
        record_id=lambda record: record,
        record_limit=2,
    )

    assert selected == ["shared", "first-only"]


def test_budget_preserves_global_ranking_when_there_are_no_axis_candidates() -> None:
    records = ["first", "second", "third"]

    assert select_records_with_axis_coverage(
        records,
        axis_candidate_ids={"unavailable": ["missing"]},
        record_id=lambda record: record,
        record_limit=2,
    ) == ["first", "second"]
