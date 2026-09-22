"""Safe, source-native parsers for JATS and TEI XML.

The standard library parser is only invoked after rejecting DTD and entity
declarations.  XML is handled as data: this module does not resolve external
resources, render XML, or fabricate PDF page numbers.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ElementTree
from hashlib import sha256
from pathlib import Path

from app.ingestion.pdf_extractor import (
    DocumentOutlineNode,
    ExtractedDocument,
    ParserIdentity,
    StructuralTextBlock,
)

_UNSAFE_XML_DECLARATION = re.compile(rb"<!\s*(?:doctype|entity)\b", re.IGNORECASE)
_XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


class NativeXmlExtractionError(RuntimeError):
    """Raised when a native XML asset is unsafe or cannot be parsed."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _source_text(element: ElementTree.Element) -> str:
    """Keep source text in document order without interpreting its markup."""

    return "".join(element.itertext()).strip()


def _xml_id(element: ElementTree.Element) -> str | None:
    return element.get(_XML_ID) or element.get("xml:id") or element.get("id")


def _config_sha256(*, source_format: str, max_file_bytes: int) -> str:
    payload = {"max_file_bytes": max_file_bytes, "source_format": source_format}
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class _NativeXmlExtractor:
    source_format: str
    parser_id: str

    def __init__(self, *, max_file_bytes: int = 25 * 1024 * 1024) -> None:
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive")
        self.max_file_bytes = max_file_bytes

    def extract(self, xml_path: Path) -> ExtractedDocument:
        path = Path(xml_path).resolve()
        if not path.is_file():
            raise NativeXmlExtractionError(f"XML not found: {path.name}")
        try:
            raw_xml = path.read_bytes()
        except OSError as exc:
            raise NativeXmlExtractionError(f"unable to read XML: {type(exc).__name__}") from exc
        if len(raw_xml) > self.max_file_bytes:
            raise NativeXmlExtractionError("XML exceeds configured size limit")
        if _UNSAFE_XML_DECLARATION.search(raw_xml):
            raise NativeXmlExtractionError("XML DTD and entity declarations are forbidden")
        try:
            root = ElementTree.fromstring(raw_xml)
        except ElementTree.ParseError as exc:
            raise NativeXmlExtractionError("unable to parse XML") from exc

        metadata, blocks, outline_nodes = self._extract(root)
        if not blocks:
            raise NativeXmlExtractionError("XML contains no extractable source text")
        character_count = sum(len(block.text) for block in blocks)
        return ExtractedDocument(
            pdf_path=str(path),
            page_count=0,
            pages=[],
            metadata=metadata,
            text_character_count=character_count,
            text_page_count=0,
            requires_ocr=False,
            source_format=self.source_format,  # type: ignore[arg-type]
            parser_identity=ParserIdentity(
                parser_id=self.parser_id,
                parser_version="1.0.0",
                contract_version="1.0.0",
                config_sha256=_config_sha256(
                    source_format=self.source_format, max_file_bytes=self.max_file_bytes
                ),
            ),
            outline_nodes=outline_nodes,
            warnings=[],
            structural_blocks=blocks,
        )

    def _extract(
        self, root: ElementTree.Element
    ) -> tuple[dict[str, str], list[StructuralTextBlock], list[DocumentOutlineNode]]:
        raise NotImplementedError

    @staticmethod
    def _block(
        *,
        kind: str,
        section_path: str,
        paragraph_number: int,
        element: ElementTree.Element,
    ) -> StructuralTextBlock | None:
        text = _source_text(element)
        if not text:
            return None
        xml_id = _xml_id(element)
        return StructuralTextBlock(
            block_id=StructuralTextBlock.make_block_id(
                kind=kind,
                section_path=section_path,
                paragraph_number=paragraph_number,
                text=text,
                xml_id=xml_id,
            ),
            kind=kind,
            section_path=section_path,
            paragraph_number=paragraph_number,
            text=text,
            xml_id=xml_id,
        )

    @staticmethod
    def _outline(
        *,
        parent_node_id: str | None,
        level: int,
        kind: str,
        title: str,
        ordinal: int,
        source_locator: str,
    ) -> DocumentOutlineNode:
        return DocumentOutlineNode(
            node_id=DocumentOutlineNode.make_node_id(
                parent_node_id=parent_node_id,
                level=level,
                kind=kind,
                title=title,
                ordinal=ordinal,
                source_locator=source_locator,
            ),
            parent_node_id=parent_node_id,
            level=level,
            kind=kind,
            title=title,
            ordinal=ordinal,
            source_locator=source_locator,
        )


class JatsXmlExtractor(_NativeXmlExtractor):
    source_format = "jats_xml"
    parser_id = "jats_xml"

    def _extract(self, root: ElementTree.Element):  # type: ignore[no-untyped-def]
        titles = [element for element in root.iter() if _local_name(element.tag) == "article-title"]
        title = _source_text(titles[0]) if titles else "Untitled JATS article"
        metadata = {"title": title}
        blocks: list[StructuralTextBlock] = []
        outline_nodes: list[DocumentOutlineNode] = []
        ordinal = 0
        for abstract_number, abstract in enumerate(
            (element for element in root.iter() if _local_name(element.tag) == "abstract"), start=1
        ):
            path = "Abstract" if abstract_number == 1 else f"Abstract {abstract_number}"
            node = self._outline(
                parent_node_id=None,
                level=1,
                kind="abstract",
                title=path,
                ordinal=ordinal,
                source_locator=f"§ {path}",
            )
            ordinal += 1
            outline_nodes.append(node)
            for paragraph_number, paragraph in enumerate(
                (item for item in abstract.iter() if _local_name(item.tag) == "p"), start=1
            ):
                block = self._block(
                    kind="abstract",
                    section_path=path,
                    paragraph_number=paragraph_number,
                    element=paragraph,
                )
                if block:
                    blocks.append(block)

        body = next(
            (element for element in root.iter() if _local_name(element.tag) == "body"), None
        )
        if body is not None:
            ordinal = self._jats_sections(body, None, [], 1, ordinal, blocks, outline_nodes)
        return metadata, blocks, outline_nodes

    def _jats_sections(
        self,
        container: ElementTree.Element,
        parent_node_id: str | None,
        parents: list[str],
        level: int,
        ordinal: int,
        blocks: list[StructuralTextBlock],
        outline_nodes: list[DocumentOutlineNode],
    ) -> int:
        for section in (child for child in container if _local_name(child.tag) == "sec"):
            title_element = next(
                (item for item in section if _local_name(item.tag) == "title"), None
            )
            title = _source_text(title_element) if title_element is not None else "Untitled section"
            path_parts = [*parents, title]
            path = " / ".join(path_parts)
            node = self._outline(
                parent_node_id=parent_node_id,
                level=level,
                kind="section",
                title=title,
                ordinal=ordinal,
                source_locator=f"§ {path}",
            )
            ordinal += 1
            outline_nodes.append(node)
            paragraph_number = 0
            for child in section:
                tag = _local_name(child.tag)
                if tag == "p":
                    paragraph_number += 1
                    block = self._block(
                        kind="paragraph",
                        section_path=path,
                        paragraph_number=paragraph_number,
                        element=child,
                    )
                    if block:
                        blocks.append(block)
                elif tag in {"table-wrap", "fig"}:
                    paragraph_number += 1
                    block = self._block(
                        kind="table" if tag == "table-wrap" else "figure",
                        section_path=path,
                        paragraph_number=paragraph_number,
                        element=child,
                    )
                    if block:
                        blocks.append(block)
            ordinal = self._jats_sections(
                section, node.node_id, path_parts, level + 1, ordinal, blocks, outline_nodes
            )
        return ordinal


class TeiXmlExtractor(_NativeXmlExtractor):
    source_format = "tei_xml"
    parser_id = "tei_xml"

    def _extract(self, root: ElementTree.Element):  # type: ignore[no-untyped-def]
        title_element = next(
            (element for element in root.iter() if _local_name(element.tag) == "title"), None
        )
        metadata = {
            "title": _source_text(title_element)
            if title_element is not None
            else "Untitled TEI document"
        }
        blocks: list[StructuralTextBlock] = []
        outline_nodes: list[DocumentOutlineNode] = []
        ordinal = 0
        for abstract in (
            element for element in root.iter() if _local_name(element.tag) == "abstract"
        ):
            path = "Abstract"
            node = self._outline(
                parent_node_id=None,
                level=1,
                kind="abstract",
                title=path,
                ordinal=ordinal,
                source_locator=f"§ {path}",
            )
            ordinal += 1
            outline_nodes.append(node)
            for index, paragraph in enumerate(
                (item for item in abstract.iter() if _local_name(item.tag) == "p"), start=1
            ):
                block = self._block(
                    kind="abstract", section_path=path, paragraph_number=index, element=paragraph
                )
                if block:
                    blocks.append(block)
        body = next(
            (element for element in root.iter() if _local_name(element.tag) == "body"), None
        )
        if body is not None:
            self._tei_divisions(body, None, [], 1, ordinal, blocks, outline_nodes)
        return metadata, blocks, outline_nodes

    def _tei_divisions(
        self,
        container: ElementTree.Element,
        parent_node_id: str | None,
        parents: list[str],
        level: int,
        ordinal: int,
        blocks: list[StructuralTextBlock],
        outline_nodes: list[DocumentOutlineNode],
    ) -> int:
        for division in (child for child in container if _local_name(child.tag) == "div"):
            head = next((item for item in division if _local_name(item.tag) == "head"), None)
            title = _source_text(head) if head is not None else "Untitled division"
            path_parts = [*parents, title]
            path = " / ".join(path_parts)
            node = self._outline(
                parent_node_id=parent_node_id,
                level=level,
                kind="section",
                title=title,
                ordinal=ordinal,
                source_locator=f"§ {path}",
            )
            ordinal += 1
            outline_nodes.append(node)
            paragraph_number = 0
            for child in division:
                tag = _local_name(child.tag)
                if tag in {"p", "table", "figure"}:
                    paragraph_number += 1
                    block = self._block(
                        kind="paragraph" if tag == "p" else tag,
                        section_path=path,
                        paragraph_number=paragraph_number,
                        element=child,
                    )
                    if block:
                        blocks.append(block)
            ordinal = self._tei_divisions(
                division, node.node_id, path_parts, level + 1, ordinal, blocks, outline_nodes
            )
        return ordinal
