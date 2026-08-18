from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.updates.harvest_closures import (
    WEEKLY_CLOSURE_DAYS,
    WeeklyHarvestClosureRegistry,
)


def test_weekly_closure_is_persisted_per_source_and_query_family(tmp_path) -> None:
    path = tmp_path / "weekly-closures.json"
    registry = WeeklyHarvestClosureRegistry(path)
    closed_at = datetime(2026, 8, 13, 12, tzinfo=UTC)

    materials = registry.close_weekly(
        profile="expand_materials_doaj_v1",
        source="doaj",
        query_set="materials",
        reason="no new accepted abstract after two thematic rotations",
        consecutive_no_gain_runs=8,
        closed_at=closed_at,
    )

    assert materials["state"] == "closed_weekly"
    assert (
        materials["closed_until"] == (closed_at + timedelta(days=WEEKLY_CLOSURE_DAYS)).isoformat()
    )
    assert (
        registry.active(
            "expand_materials_doaj_v1",
            now=closed_at + timedelta(days=6),
        )
        == materials
    )
    assert registry.active("expand_focused_doaj_v1", now=closed_at) is None


def test_weekly_closure_expires_and_is_removed(tmp_path) -> None:
    registry = WeeklyHarvestClosureRegistry(tmp_path / "weekly-closures.json")
    closed_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    registry.close_weekly(
        profile="expand_materials_doaj_v1",
        source="doaj",
        query_set="materials",
        reason="no useful additions",
        consecutive_no_gain_runs=8,
        closed_at=closed_at,
    )

    assert (
        registry.active(
            "expand_materials_doaj_v1",
            now=closed_at + timedelta(days=7),
        )
        is None
    )
    assert registry.active_entries(now=closed_at + timedelta(days=7)) == []


def test_new_gain_clears_only_the_matching_weekly_closure(tmp_path) -> None:
    registry = WeeklyHarvestClosureRegistry(tmp_path / "weekly-closures.json")
    closed_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    for query_set in ("materials", "focused"):
        registry.close_weekly(
            profile=f"expand_{query_set}_doaj_v1",
            source="doaj",
            query_set=query_set,
            reason="no useful additions",
            consecutive_no_gain_runs=8,
            closed_at=closed_at,
        )

    assert registry.clear("expand_materials_doaj_v1")
    assert registry.active("expand_materials_doaj_v1", now=closed_at) is None
    assert registry.active("expand_focused_doaj_v1", now=closed_at) is not None
