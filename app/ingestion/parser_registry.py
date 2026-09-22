"""Lazy registry for explicitly selected document parsers.

External parser adapters are intentionally represented as unavailable capabilities
until their separate packaging and security gates have been completed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import IngestionConfig

ParserMode = Literal["pymupdf", "docling", "ragflow_deepdoc", "auto_experimental"]
CapabilityStatus = Literal["available", "unavailable", "unknown"]


class ParserDiagnostic(BaseModel):
    """Stable, bounded diagnostic that deliberately contains no document data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal["unknown_parser", "parser_unavailable"]
    parser_id: str = Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")


class ParserCapability(BaseModel):
    """Known registry entry; availability checks do not import optional packages."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parser_id: str = Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    status: CapabilityStatus
    diagnostic: ParserDiagnostic | None = None


class ParserRegistryError(RuntimeError):
    def __init__(self, diagnostic: ParserDiagnostic) -> None:
        super().__init__(f"{diagnostic.code}:{diagnostic.parser_id}")
        self.diagnostic = diagnostic


ParserFactory = Callable[[IngestionConfig], object]


def _create_pymupdf(config: IngestionConfig) -> object:
    """Import the builtin adapter only when it was selected for construction."""

    from app.ingestion.pdf_extractor import PyMuPdfExtractor

    return PyMuPdfExtractor(
        min_page_text_characters=config.min_page_text_characters,
        min_text_page_ratio=config.min_text_page_ratio,
    )


class ParserRegistry:
    """Expose known parser capabilities without probing or importing external software."""

    def __init__(self) -> None:
        self._factories: dict[ParserMode, ParserFactory] = {"pymupdf": _create_pymupdf}
        self._capabilities: dict[ParserMode, ParserCapability] = {
            "pymupdf": ParserCapability(parser_id="pymupdf", status="available"),
            "docling": ParserCapability(
                parser_id="docling",
                status="unavailable",
                diagnostic=ParserDiagnostic(code="parser_unavailable", parser_id="docling"),
            ),
            "ragflow_deepdoc": ParserCapability(
                parser_id="ragflow_deepdoc",
                status="unavailable",
                diagnostic=ParserDiagnostic(code="parser_unavailable", parser_id="ragflow_deepdoc"),
            ),
            "auto_experimental": ParserCapability(
                parser_id="auto_experimental",
                status="unknown",
                diagnostic=ParserDiagnostic(
                    code="parser_unavailable", parser_id="auto_experimental"
                ),
            ),
        }

    def capability(self, parser_id: str) -> ParserCapability:
        if parser_id in self._capabilities:
            return self._capabilities[parser_id]  # type: ignore[index]
        return ParserCapability(
            parser_id=parser_id,
            status="unknown",
            diagnostic=ParserDiagnostic(code="unknown_parser", parser_id=parser_id),
        )

    def register_factory(self, parser_id: ParserMode, factory: ParserFactory) -> None:
        """Register a tested adapter once; optional packages are still never imported here."""

        if parser_id in self._factories:
            raise ValueError(f"parser factory already registered: {parser_id}")
        self._factories[parser_id] = factory
        self._capabilities[parser_id] = ParserCapability(parser_id=parser_id, status="available")

    def create(self, config: IngestionConfig) -> object:
        selection = config.parser
        capability = self.capability(selection.mode)
        factory = self._factories.get(selection.mode)
        if capability.status != "available" or factory is None:
            diagnostic = capability.diagnostic or ParserDiagnostic(
                code="parser_unavailable", parser_id=selection.mode
            )
            raise ParserRegistryError(diagnostic)
        return factory(config)
