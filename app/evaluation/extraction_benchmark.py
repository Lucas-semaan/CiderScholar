"""Strict, offline contracts for the frozen extraction-parser benchmark."""

from __future__ import annotations

from collections import Counter
from pathlib import Path, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ExtractionBenchmarkCategory = Literal[
    "text_simple",
    "multi_column",
    "table_rich",
    "scanned_ocr",
    "figure_rich",
    "difficult_invalid",
]
ManifestState = Literal["draft", "final"]
ExpectedDocumentOutcome = Literal["extracted", "ocr_required", "invalid"]
ExpectedElementKind = Literal["text", "heading", "table", "figure", "caption"]


class ExpectedTextFragment(BaseModel):
    """One ordered, human-transcribed source fragment from an annotated page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reading_order: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=10000)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("expected text cannot be blank")
        return cleaned


class ExpectedTableCell(BaseModel):
    """A human-checked expected table cell, addressed with zero-based coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    table_element_id: str = Field(pattern=r"^element-[a-z0-9][a-z0-9-]{1,79}$")
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    content: str | None = Field(default=None, max_length=10000)
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("expected table cell content cannot be blank")
        return cleaned


class ExpectedFigure(BaseModel):
    """A figure and its optional human-transcribed source caption."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    figure_element_id: str = Field(pattern=r"^element-[a-z0-9][a-z0-9-]{1,79}$")
    expected_caption: str | None = Field(default=None, max_length=10000)

    @field_validator("expected_caption")
    @classmethod
    def strip_caption(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("expected figure caption cannot be blank")
        return cleaned


class ExpectedSection(BaseModel):
    """A source-document section expected on one annotated page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^section-[a-z0-9][a-z0-9-]{1,79}$")
    title: str = Field(min_length=1, max_length=500)
    level: int = Field(ge=1, le=12)
    reading_order: int = Field(ge=1)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("section title cannot be blank")
        return cleaned


class ExpectedElement(BaseModel):
    """A source element and its human-annotated position in reading order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^element-[a-z0-9][a-z0-9-]{1,79}$")
    kind: ExpectedElementKind
    reading_order: int = Field(ge=1)
    section_id: str | None = Field(default=None, pattern=r"^section-[a-z0-9][a-z0-9-]{1,79}$")


class PageAnnotation(BaseModel):
    """Human ground truth for one real, one-based PDF page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    sections: list[ExpectedSection] = Field(default_factory=list)
    elements: list[ExpectedElement] = Field(default_factory=list)
    expected_text: str | None = Field(default=None, max_length=50000)
    expected_text_fragments: list[ExpectedTextFragment] = Field(
        default_factory=list, max_length=200
    )
    table_cells: list[ExpectedTableCell] = Field(default_factory=list, max_length=2000)
    figures: list[ExpectedFigure] = Field(default_factory=list, max_length=200)
    missing_text_expected: bool = False
    duplicated_text_expected: bool = False

    @field_validator("expected_text")
    @classmethod
    def strip_expected_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("expected_text cannot be blank")
        return cleaned

    @model_validator(mode="after")
    def coherent_page_annotation(self) -> PageAnnotation:
        section_ids = [section.id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("section identifiers must be unique on a page")
        element_ids = [element.id for element in self.elements]
        if len(element_ids) != len(set(element_ids)):
            raise ValueError("element identifiers must be unique on a page")
        orders = [element.reading_order for element in self.elements]
        if sorted(orders) != list(range(1, len(orders) + 1)):
            raise ValueError("element reading_order must be contiguous from one")
        section_orders = [section.reading_order for section in self.sections]
        if len(section_orders) != len(set(section_orders)):
            raise ValueError("section reading_order must be unique on a page")
        known_sections = set(section_ids)
        if any(
            element.section_id not in known_sections
            for element in self.elements
            if element.section_id
        ):
            raise ValueError("element section_id must reference a section on the same page")
        if self.expected_text is not None and self.expected_text_fragments:
            raise ValueError("expected_text and expected_text_fragments are mutually exclusive")
        fragment_orders = [fragment.reading_order for fragment in self.expected_text_fragments]
        if sorted(fragment_orders) != list(range(1, len(fragment_orders) + 1)):
            raise ValueError("expected text fragment order must be contiguous from one")
        element_kinds = {element.id: element.kind for element in self.elements}
        table_ids = {element_id for element_id, kind in element_kinds.items() if kind == "table"}
        figure_ids = {element_id for element_id, kind in element_kinds.items() if kind == "figure"}
        if any(cell.table_element_id not in table_ids for cell in self.table_cells):
            raise ValueError("table cells must reference an element with kind=table")
        coordinates = [(cell.table_element_id, cell.row, cell.column) for cell in self.table_cells]
        if len(coordinates) != len(set(coordinates)):
            raise ValueError("table cell coordinates must be unique")
        occupied: set[tuple[str, int, int]] = set()
        for cell in self.table_cells:
            for row in range(cell.row, cell.row + cell.row_span):
                for column in range(cell.column, cell.column + cell.column_span):
                    coordinate = (cell.table_element_id, row, column)
                    if coordinate in occupied:
                        raise ValueError("table cell spans must not overlap")
                    occupied.add(coordinate)
        figure_ids_annotated = [figure.figure_element_id for figure in self.figures]
        if len(figure_ids_annotated) != len(set(figure_ids_annotated)):
            raise ValueError("figure annotations must be unique on a page")
        if any(figure_id not in figure_ids for figure_id in figure_ids_annotated):
            raise ValueError("figure captions must reference an element with kind=figure")
        return self


class ExpectedDocumentResult(BaseModel):
    """Document-level ground truth, deliberately independent from parser outputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: ExpectedDocumentOutcome
    page_count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def coherent_outcome(self) -> ExpectedDocumentResult:
        if self.outcome == "invalid" and self.page_count is not None:
            raise ValueError("invalid documents cannot declare a page_count")
        if self.outcome != "invalid" and self.page_count is None:
            raise ValueError("extractable documents require a page_count")
        return self


class ExtractionBenchmarkEntry(BaseModel):
    """One asset identity and source-derived benchmark annotation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^extract-[a-z0-9][a-z0-9-]{2,79}$")
    category: ExtractionBenchmarkCategory
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    local_path: str | None = Field(default=None, max_length=240)
    redistributable: bool
    license: str | None = Field(default=None, max_length=300)
    expected_document_result: ExpectedDocumentResult
    page_annotations: list[PageAnnotation] = Field(default_factory=list)

    @field_validator("local_path")
    @classmethod
    def controlled_local_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("local_path cannot be blank")
        windows_path = PureWindowsPath(cleaned)
        if windows_path.is_absolute() or windows_path.drive or ".." in windows_path.parts:
            raise ValueError("local_path must be a relative, non-traversing path")
        if "\\" in cleaned or not cleaned.lower().endswith(".pdf"):
            raise ValueError("local_path must be a relative POSIX path to a PDF")
        return cleaned

    @field_validator("license")
    @classmethod
    def strip_license(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("license cannot be blank")
        return cleaned

    @model_validator(mode="after")
    def coherent_entry(self) -> ExtractionBenchmarkEntry:
        if self.redistributable and self.license is None:
            raise ValueError("redistributable entries require a license")
        pages = [annotation.page_number for annotation in self.page_annotations]
        if pages != sorted(pages) or len(pages) != len(set(pages)):
            raise ValueError("page annotations must have unique ascending real page numbers")
        element_ids = [element.id for page in self.page_annotations for element in page.elements]
        if len(element_ids) != len(set(element_ids)):
            raise ValueError("element identifiers must be unique within an entry")
        result = self.expected_document_result
        if result.outcome == "invalid":
            if self.page_annotations:
                raise ValueError("invalid documents cannot have page annotations")
        else:
            if not self.page_annotations:
                raise ValueError("extractable documents require page annotations")
            if any(page > result.page_count for page in pages):
                raise ValueError("page annotation exceeds expected page_count")
        if self.category == "scanned_ocr" and result.outcome != "ocr_required":
            raise ValueError("scanned_ocr entries must require OCR")
        if self.category == "difficult_invalid" and result.outcome != "invalid":
            raise ValueError("difficult_invalid entries must be invalid")
        if (
            self.category not in {"scanned_ocr", "difficult_invalid"}
            and result.outcome != "extracted"
        ):
            raise ValueError("text, table, and figure categories must be natively extractable")
        return self


class ExtractionBenchmarkManifest(BaseModel):
    """A draft or frozen final benchmark manifest; no candidate parser output is admitted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    benchmark_version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
    state: ManifestState
    annotations_human_source_reviewed: bool
    annotations_frozen_before_candidate_outputs: bool
    entries: list[ExtractionBenchmarkEntry] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def coherent_manifest(self) -> ExtractionBenchmarkManifest:
        identifiers = [entry.id for entry in self.entries]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("benchmark entry identifiers must be unique")
        hashes = [entry.sha256 for entry in self.entries]
        if len(hashes) != len(set(hashes)):
            raise ValueError("benchmark SHA-256 values must be unique")
        if self.state == "final":
            counts = Counter(entry.category for entry in self.entries)
            if len(self.entries) != 60 or any(counts[category] != 10 for category in _CATEGORIES):
                raise ValueError("final manifests require exactly ten entries in every category")
            if not self.annotations_human_source_reviewed:
                raise ValueError("final manifests require human source review attestation")
            if not self.annotations_frozen_before_candidate_outputs:
                raise ValueError("final manifests require pre-candidate freeze attestation")
            for entry in self.entries:
                if entry.expected_document_result.outcome == "invalid":
                    continue
                for page in entry.page_annotations:
                    if page.expected_text is None and not page.expected_text_fragments:
                        raise ValueError(
                            "final manifests require expected text on every annotated page"
                        )
                    if any(cell.content is None for cell in page.table_cells):
                        raise ValueError("final manifests require expected table cell content")
            table_entries = [entry for entry in self.entries if entry.category == "table_rich"]
            if any(
                not page.table_cells
                for entry in table_entries
                for page in entry.page_annotations
                if any(element.kind == "table" for element in page.elements)
            ) or any(
                not any(
                    element.kind == "table"
                    for page in entry.page_annotations
                    for element in page.elements
                )
                for entry in table_entries
            ):
                raise ValueError("final table_rich entries require annotated table cells")
            figure_entries = [entry for entry in self.entries if entry.category == "figure_rich"]
            if any(
                not any(page.figures for page in entry.page_annotations)
                or not any(
                    figure.expected_caption is not None
                    for page in entry.page_annotations
                    for figure in page.figures
                )
                for entry in figure_entries
            ):
                raise ValueError(
                    "each final figure_rich entry requires an annotated figure and expected caption"
                )
        return self


_CATEGORIES: tuple[ExtractionBenchmarkCategory, ...] = (
    "text_simple",
    "multi_column",
    "table_rich",
    "scanned_ocr",
    "figure_rich",
    "difficult_invalid",
)


def load_extraction_benchmark_manifest(path: str | Path) -> ExtractionBenchmarkManifest:
    """Load and validate a manifest without reading its optional PDF paths."""

    source = Path(path)
    return ExtractionBenchmarkManifest.model_validate_json(source.read_text(encoding="utf-8"))
