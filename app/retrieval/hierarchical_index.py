"""Read-only article -> section -> chunk navigation over authoritative SQLite rows."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.database.sqlite import Database


class HierarchicalSelectionTrace(BaseModel):
    """Non-textual audit of one bounded intra-article navigation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    article_id: str = Field(min_length=1, max_length=200)
    anchor_chunk_ids: list[int] = Field(default_factory=list, max_length=8)
    selected_section_names: list[str] = Field(default_factory=list, max_length=20)
    selected_chunk_ids: list[int] = Field(default_factory=list, max_length=100)


class SqliteHierarchicalIndex:
    """Expose hierarchy without copying article text into another authority."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def navigate(
        self,
        *,
        article_id: str,
        anchor_chunk_ids: Sequence[int],
        candidate_limit: int,
        neighborhood_radius: int,
        include_methods: bool,
    ) -> tuple[list[sqlite3.Row], HierarchicalSelectionTrace]:
        rows = self.database.hierarchical_chunks_for_article(
            article_id,
            anchor_chunk_ids=anchor_chunk_ids,
            neighborhood_radius=neighborhood_radius,
            limit=candidate_limit,
            include_methods=include_methods,
        )
        sections = list(
            dict.fromkeys(
                str(row["section"])
                for row in rows
                if row["section"] is not None and str(row["section"]).strip()
            )
        )
        return rows, HierarchicalSelectionTrace(
            article_id=article_id,
            anchor_chunk_ids=list(dict.fromkeys(int(value) for value in anchor_chunk_ids))[:8],
            selected_section_names=sections[:20],
            selected_chunk_ids=[int(row["id"]) for row in rows],
        )
