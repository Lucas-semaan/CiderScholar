"""Deterministic, source-native table evidence and numeric checks."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, model_validator

_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?\s*%?")


class TableEvidenceCell(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=10_000)


class TableEvidence(BaseModel):
    """A citable table projection; no generated caption or cell is accepted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    element_id: str = Field(min_length=1, max_length=256)
    article_id: str = Field(min_length=1, max_length=128)
    page_number: int = Field(ge=1)
    cells: tuple[TableEvidenceCell, ...] = Field(min_length=1, max_length=2_000)
    related_chunk_ids: tuple[int, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def unique_cells(self) -> TableEvidence:
        coordinates = {(cell.row_index, cell.column_index) for cell in self.cells}
        if len(coordinates) != len(self.cells):
            raise ValueError("table evidence cells cannot be duplicated")
        return self

    def contains_numeric_claim(self, value: str) -> bool:
        """Accept only a numeric token that appears in a persisted source cell."""

        target = _decimal(value)
        if target is None:
            return False
        return any(target in _numbers(cell.text) for cell in self.cells)


def _decimal(value: str) -> Decimal | None:
    token = value.strip().replace(" ", "")
    if token.endswith("%"):
        token = token[:-1]
    try:
        return Decimal(token.replace(",", "."))
    except InvalidOperation:
        return None


def _numbers(text: str) -> set[Decimal]:
    return {number for match in _NUMBER.findall(text) if (number := _decimal(match)) is not None}


def table_evidence_from_element(element: dict[str, object]) -> TableEvidence:
    """Convert only a persisted table element; figures and generated fields are rejected."""

    if element.get("kind") != "table":
        raise ValueError("table evidence requires a source table element")
    raw_cells = element.get("cells")
    if not isinstance(raw_cells, list):
        raise ValueError("source table cells are unavailable")
    relations = element.get("text_relations")
    related = (
        tuple(
            int(item["related_chunk_id"])
            for item in relations
            if isinstance(item, dict) and item.get("related_chunk_id") is not None
        )
        if isinstance(relations, list)
        else ()
    )
    return TableEvidence(
        element_id=str(element["id"]),
        article_id=str(element["article_id"]),
        page_number=int(element["page_number"]),
        cells=tuple(TableEvidenceCell.model_validate(cell) for cell in raw_cells),
        related_chunk_ids=tuple(dict.fromkeys(related)),
    )
