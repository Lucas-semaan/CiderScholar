"""Page-by-page PDF extraction behind a replaceable interface."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PdfExtractionError(RuntimeError):
    """Raised when a PDF cannot be opened or parsed."""


class ParserIdentity(BaseModel):
    """Versioned identity required to reproduce a parser-derived result."""

    model_config = ConfigDict(extra="forbid")

    parser_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    parser_version: str = Field(min_length=1, max_length=128)
    contract_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_name: str | None = Field(default=None, min_length=1, max_length=256)
    model_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("parser_version")
    @classmethod
    def parser_version_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("parser version cannot be blank")
        return value

    @field_validator("model_name")
    @classmethod
    def model_name_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("model name cannot be blank")
        return value

    @model_validator(mode="after")
    def complete_model_identity(self) -> ParserIdentity:
        if (self.model_name is None) != (self.model_sha256 is None):
            raise ValueError("model name and SHA-256 must be provided together")
        return self


class ExtractionWarning(BaseModel):
    """Bounded, non-scientific diagnostic emitted by a parser."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    severity: Literal["info", "review", "blocking"]
    page_number: int | None = Field(default=None, ge=1)
    element_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$",
    )


class DocumentOutlineNode(BaseModel):
    """Source-derived structural node with a deterministic identity."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(pattern=r"^outline-[a-f0-9]{24}$")
    parent_node_id: str | None = Field(default=None, pattern=r"^outline-[a-f0-9]{24}$")
    level: int = Field(ge=1, le=32)
    kind: Literal["section", "heading", "abstract", "references", "appendix", "other"]
    title: str = Field(min_length=1, max_length=4_000)
    ordinal: int = Field(ge=0)
    source_locator: str = Field(min_length=1, max_length=2_000)

    @field_validator("title", "source_locator")
    @classmethod
    def source_text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("outline source fields cannot be blank")
        return value

    @classmethod
    def make_node_id(
        cls,
        *,
        parent_node_id: str | None,
        level: int,
        kind: str,
        title: str,
        ordinal: int,
        source_locator: str,
    ) -> str:
        """Build an ID from only persisted, source-derived node fields."""

        payload = {
            "kind": kind,
            "level": level,
            "ordinal": ordinal,
            "parent_node_id": parent_node_id,
            "source_locator": source_locator,
            "title": title,
        }
        digest = sha256(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
        ).hexdigest()
        return f"outline-{digest[:24]}"

    @model_validator(mode="after")
    def deterministic_identity(self) -> DocumentOutlineNode:
        expected = self.make_node_id(
            parent_node_id=self.parent_node_id,
            level=self.level,
            kind=self.kind,
            title=self.title,
            ordinal=self.ordinal,
            source_locator=self.source_locator,
        )
        if self.node_id != expected:
            raise ValueError("outline node ID does not match its source-derived fields")
        return self


@dataclass(slots=True)
class PageText:
    page_number: int
    text: str
    source_kind: Literal["pdf_embedded", "windows_ocr"] = "pdf_embedded"
    ocr_language: str | None = None
    ocr_confidence: float | None = None


class OcrPageTrace(BaseModel):
    """Local OCR audit; rejected text is retained here but excluded from PageText."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    language: str = Field(min_length=2, max_length=35)
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_method: Literal["heuristic_text_quality_v1"] = "heuristic_text_quality_v1"
    embedded_text_original: str = Field(max_length=100_000)
    ocr_text: str = Field(max_length=100_000)
    admitted: bool
    decision_reason: Literal["ocr_confident", "ocr_low_confidence", "ocr_empty"]

    @model_validator(mode="after")
    def coherent_decision(self) -> OcrPageTrace:
        if self.admitted != (self.decision_reason == "ocr_confident"):
            raise ValueError("OCR admission and decision reason are inconsistent")
        if self.decision_reason == "ocr_empty" and self.ocr_text.strip():
            raise ValueError("non-empty OCR text cannot have the empty decision")
        return self


class TableCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    text: str = Field(max_length=10_000)


class ElementTextRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation: Literal["nearest_page_text"] = "nearest_page_text"
    page_number: int = Field(ge=1)
    source_excerpt: str = Field(min_length=1, max_length=2_000)


class ScientificDocumentElement(BaseModel):
    """Source-native table or figure; synthetic enrichment stays a separate field."""

    model_config = ConfigDict(extra="forbid")

    element_id: str = Field(pattern=r"^(table|figure)-p[0-9]{4}-[0-9]{3}$")
    kind: Literal["table", "figure"]
    page_number: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    source_kind: Literal["pdf_embedded", "windows_ocr"]
    source_locator: str | None = Field(default=None, max_length=200)
    original_caption: str | None = Field(default=None, max_length=4_000)
    synthetic_caption: str | None = Field(default=None, max_length=4_000)
    cells: list[TableCell] = Field(default_factory=list, max_length=2_000)
    text_relations: list[ElementTextRelation] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def coherent_source_element(self) -> ScientificDocumentElement:
        x0, y0, x1, y1 = self.bbox
        if x1 <= x0 or y1 <= y0:
            raise ValueError("document element bounding box is invalid")
        if self.kind == "figure" and self.cells:
            raise ValueError("figure cannot contain table cells")
        if self.synthetic_caption is not None:
            raise ValueError("source extraction cannot create a synthetic caption")
        identities = {(item.row_index, item.column_index) for item in self.cells}
        if len(identities) != len(self.cells):
            raise ValueError("table cells cannot be duplicated")
        return self


class StructuralTextBlock(BaseModel):
    """A source-native XML span with a structural, never paginated, locator."""

    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(pattern=r"^struct-[a-f0-9]{24}$")
    kind: Literal["abstract", "paragraph", "table", "figure", "caption"]
    section_path: str = Field(min_length=1, max_length=2_000)
    paragraph_number: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=1_000_000)
    xml_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9._:-]{0,255}$",
    )

    @classmethod
    def make_block_id(
        cls,
        *,
        kind: str,
        section_path: str,
        paragraph_number: int,
        text: str,
        xml_id: str | None,
    ) -> str:
        payload = {
            "kind": kind,
            "paragraph_number": paragraph_number,
            "section_path": section_path,
            "text": text,
            "xml_id": xml_id,
        }
        digest = sha256(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
        ).hexdigest()
        return f"struct-{digest[:24]}"

    @model_validator(mode="after")
    def deterministic_identity(self) -> StructuralTextBlock:
        expected = self.make_block_id(
            kind=self.kind,
            section_path=self.section_path,
            paragraph_number=self.paragraph_number,
            text=self.text,
            xml_id=self.xml_id,
        )
        if self.block_id != expected:
            raise ValueError("structural block ID does not match its source-derived fields")
        return self


@dataclass(slots=True)
class ExtractedDocument:
    pdf_path: str
    page_count: int
    pages: list[PageText]
    metadata: dict[str, str]
    text_character_count: int
    text_page_count: int
    requires_ocr: bool
    elements: list[ScientificDocumentElement] = field(default_factory=list)
    ocr_pages: list[OcrPageTrace] = field(default_factory=list)
    source_format: Literal[
        "pdf",
        "jats_xml",
        "tei_xml",
        "structured_xml",
        "cleaned_text",
        "plain_text",
    ] = "pdf"
    parser_identity: ParserIdentity | None = None
    outline_nodes: list[DocumentOutlineNode] = field(default_factory=list)
    warnings: list[ExtractionWarning] = field(default_factory=list)
    structural_blocks: list[StructuralTextBlock] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.source_format not in {
            "pdf",
            "jats_xml",
            "tei_xml",
            "structured_xml",
            "cleaned_text",
            "plain_text",
        }:
            raise ValueError("source format is unsupported")
        self._validate_outline_nodes()
        block_ids = {block.block_id for block in self.structural_blocks}
        if len(block_ids) != len(self.structural_blocks):
            raise ValueError("structural block IDs cannot be duplicated")

    def _validate_outline_nodes(self) -> None:
        nodes_by_id = {node.node_id: node for node in self.outline_nodes}
        if len(nodes_by_id) != len(self.outline_nodes):
            raise ValueError("outline node IDs cannot be duplicated")
        for node in self.outline_nodes:
            if node.parent_node_id is not None and node.parent_node_id not in nodes_by_id:
                raise ValueError("outline node parent is missing")
        for node in self.outline_nodes:
            visited: set[str] = set()
            current = node
            while current.parent_node_id is not None:
                if current.node_id in visited:
                    raise ValueError("outline nodes cannot contain a cycle")
                visited.add(current.node_id)
                current = nodes_by_id[current.parent_node_id]
        ordinals = [node.ordinal for node in self.outline_nodes]
        if any(later <= earlier for earlier, later in zip(ordinals, ordinals[1:], strict=False)):
            raise ValueError("outline ordinals must be unique and strictly increasing")
        positions = {node.node_id: index for index, node in enumerate(self.outline_nodes)}
        for node in self.outline_nodes:
            if node.parent_node_id is None:
                if node.level != 1:
                    raise ValueError("outline root node must have level one")
                continue
            parent = nodes_by_id[node.parent_node_id]
            if positions[parent.node_id] >= positions[node.node_id]:
                raise ValueError("outline parent must precede its child")
            if node.level <= parent.level:
                raise ValueError("outline child level must exceed its parent level")

    def to_dict(self) -> dict[str, Any]:
        return {
            "pdf_path": self.pdf_path,
            "page_count": self.page_count,
            "pages": [asdict(page) for page in self.pages],
            "metadata": self.metadata,
            "text_character_count": self.text_character_count,
            "text_page_count": self.text_page_count,
            "requires_ocr": self.requires_ocr,
            "elements": [element.model_dump(mode="json") for element in self.elements],
            "ocr_pages": [trace.model_dump(mode="json") for trace in self.ocr_pages],
            "source_format": self.source_format,
            "parser_identity": (
                self.parser_identity.model_dump(mode="json")
                if self.parser_identity is not None
                else None
            ),
            "outline_nodes": [node.model_dump(mode="json") for node in self.outline_nodes],
            "warnings": [warning.model_dump(mode="json") for warning in self.warnings],
            "structural_blocks": [
                block.model_dump(mode="json") for block in self.structural_blocks
            ],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExtractedDocument:
        return cls(
            pdf_path=str(value["pdf_path"]),
            page_count=int(value["page_count"]),
            pages=[PageText(**page) for page in value["pages"]],
            metadata={str(k): str(v) for k, v in value.get("metadata", {}).items()},
            text_character_count=int(value["text_character_count"]),
            text_page_count=int(value["text_page_count"]),
            requires_ocr=bool(value["requires_ocr"]),
            elements=[
                ScientificDocumentElement.model_validate(element)
                for element in value.get("elements", [])
            ],
            ocr_pages=[OcrPageTrace.model_validate(trace) for trace in value.get("ocr_pages", [])],
            source_format=str(value.get("source_format", "pdf")),
            parser_identity=(
                ParserIdentity.model_validate(value["parser_identity"])
                if value.get("parser_identity") is not None
                else None
            ),
            outline_nodes=[
                DocumentOutlineNode.model_validate(node) for node in value.get("outline_nodes", [])
            ],
            warnings=[
                ExtractionWarning.model_validate(warning) for warning in value.get("warnings", [])
            ],
            structural_blocks=[
                StructuralTextBlock.model_validate(block)
                for block in value.get("structural_blocks", [])
            ],
        )


class PdfExtractor(Protocol):
    """Interface that can later be implemented with GROBID or OCR tools."""

    def extract(self, pdf_path: Path) -> ExtractedDocument: ...


def sorted_page_text(page: Any) -> str:
    """Preserve block reading order without PyMuPDF's pathological line sort cases."""

    return (
        "\n".join(
            str(block[4])
            for block in page.get_text("blocks", sort=True)
            if len(block) >= 5 and str(block[4]).strip()
        )
        .replace("\x00", "")
        .strip()
    )


def pymupdf_config_sha256(*, min_page_text_characters: int, min_text_page_ratio: float) -> str:
    """Hash every PyMuPDF extraction setting that can change source-derived text."""

    payload = {
        "min_page_text_characters": min_page_text_characters,
        "min_text_page_ratio": min_text_page_ratio,
    }
    return sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


class PyMuPdfExtractor:
    def __init__(
        self,
        *,
        min_page_text_characters: int = 25,
        min_text_page_ratio: float = 0.15,
    ) -> None:
        self.min_page_text_characters = min_page_text_characters
        self.min_text_page_ratio = min_text_page_ratio

    def extract(self, pdf_path: Path) -> ExtractedDocument:
        try:
            import fitz
        except ImportError as exc:  # pragma: no cover - depends on installation
            raise RuntimeError(
                "PyMuPDF is required for PDF extraction: pip install PyMuPDF"
            ) from exc

        path = Path(pdf_path).resolve()
        pages: list[PageText] = []
        total_characters = 0
        text_page_count = 0
        elements: list[ScientificDocumentElement] = []

        try:
            with fitz.open(path) as document:
                if document.needs_pass:
                    raise PdfExtractionError("encrypted PDF requires a password")
                page_count = document.page_count
                metadata = {
                    str(key): str(value)
                    for key, value in (document.metadata or {}).items()
                    if value not in (None, "")
                }
                for index in range(page_count):
                    # Only the current page object and its extracted text are materialized.
                    page = document.load_page(index)
                    text = sorted_page_text(page)
                    pages.append(PageText(page_number=index + 1, text=text))
                    elements.extend(self._extract_elements(page, index + 1))
                    character_count = len(text)
                    total_characters += character_count
                    if character_count >= self.min_page_text_characters:
                        text_page_count += 1
                    page = None
        except PdfExtractionError:
            raise
        except Exception as exc:
            raise PdfExtractionError(f"unable to parse PDF: {type(exc).__name__}") from exc

        text_ratio = text_page_count / page_count if page_count else 0.0
        requires_ocr = (
            page_count == 0
            or total_characters < self.min_page_text_characters
            or text_ratio < self.min_text_page_ratio
        )
        return ExtractedDocument(
            pdf_path=str(path),
            page_count=page_count,
            pages=pages,
            metadata=metadata,
            text_character_count=total_characters,
            text_page_count=text_page_count,
            requires_ocr=requires_ocr,
            elements=elements,
            source_format="pdf",
            parser_identity=ParserIdentity(
                parser_id="pymupdf",
                parser_version=str(fitz.VersionBind),
                contract_version="1.0.0",
                config_sha256=pymupdf_config_sha256(
                    min_page_text_characters=self.min_page_text_characters,
                    min_text_page_ratio=self.min_text_page_ratio,
                ),
            ),
            outline_nodes=[],
            warnings=[],
        )

    @staticmethod
    def _extract_elements(page: Any, page_number: int) -> list[ScientificDocumentElement]:
        blocks = [
            (
                (float(block[0]), float(block[1]), float(block[2]), float(block[3])),
                line.strip(),
            )
            for block in page.get_text("blocks", sort=True)
            if len(block) >= 5
            for line in str(block[4]).replace("\x00", "").splitlines()
            if line.strip()
        ]
        elements: list[ScientificDocumentElement] = []
        try:
            tables = list(page.find_tables().tables)
        except Exception:
            tables = []
        for table_index, table in enumerate(tables, start=1):
            bbox = tuple(float(value) for value in table.bbox)
            rows = table.extract()
            cells = [
                TableCell(
                    row_index=row_index,
                    column_index=column_index,
                    text=str(value or "").strip(),
                )
                for row_index, row in enumerate(rows)
                for column_index, value in enumerate(row)
            ]
            caption, relations = _caption_and_relations(
                blocks,
                bbox,
                kind="table",
                page_number=page_number,
            )
            elements.append(
                ScientificDocumentElement(
                    element_id=f"table-p{page_number:04d}-{table_index:03d}",
                    kind="table",
                    page_number=page_number,
                    bbox=bbox,
                    source_kind="pdf_embedded",
                    source_locator=f"table-index:{table_index}",
                    original_caption=caption,
                    cells=cells,
                    text_relations=relations,
                )
            )
        figure_index = 0
        seen_rectangles: set[tuple[float, float, float, float]] = set()
        for image in page.get_images(full=True):
            xref = int(image[0])
            for rectangle in page.get_image_rects(xref):
                bbox = tuple(round(float(value), 3) for value in rectangle)
                if bbox in seen_rectangles or bbox[2] - bbox[0] < 20 or bbox[3] - bbox[1] < 20:
                    continue
                seen_rectangles.add(bbox)
                figure_index += 1
                caption, relations = _caption_and_relations(
                    blocks,
                    bbox,
                    kind="figure",
                    page_number=page_number,
                )
                elements.append(
                    ScientificDocumentElement(
                        element_id=f"figure-p{page_number:04d}-{figure_index:03d}",
                        kind="figure",
                        page_number=page_number,
                        bbox=bbox,
                        source_kind="pdf_embedded",
                        source_locator=f"image-xref:{xref}",
                        original_caption=caption,
                        text_relations=relations,
                    )
                )
        return elements


def _overlaps(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return not (
        first[2] <= second[0]
        or first[0] >= second[2]
        or first[3] <= second[1]
        or first[1] >= second[3]
    )


def _caption_and_relations(
    blocks: list[tuple[tuple[float, float, float, float], str]],
    bbox: tuple[float, float, float, float],
    *,
    kind: Literal["table", "figure"],
    page_number: int,
) -> tuple[str | None, list[ElementTextRelation]]:
    prefixes = ("table", "tableau") if kind == "table" else ("figure", "fig.", "fig ")
    outside = [(block_bbox, text) for block_bbox, text in blocks if not _overlaps(block_bbox, bbox)]

    def distance(item: tuple[tuple[float, float, float, float], str]) -> float:
        block_bbox, _text = item
        return min(abs(block_bbox[1] - bbox[3]), abs(bbox[1] - block_bbox[3]))

    caption_candidates = [
        item
        for item in outside
        if item[1].casefold().lstrip().startswith(prefixes) and distance(item) <= 120
    ]
    caption = min(caption_candidates, key=distance)[1] if caption_candidates else None
    all_caption_prefixes = ("table", "tableau", "figure", "fig.", "fig ")
    related_candidates = [
        item
        for item in outside
        if item[1] != caption
        and len(item[1]) >= 20
        and not item[1].casefold().lstrip().startswith(all_caption_prefixes)
    ]
    relations = (
        [
            ElementTextRelation(
                page_number=page_number,
                source_excerpt=min(related_candidates, key=distance)[1][:2_000],
            )
        ]
        if related_candidates
        else []
    )
    return caption, relations
