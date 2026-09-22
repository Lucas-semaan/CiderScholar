from __future__ import annotations

from pathlib import Path

import pytest

from app.config import IngestionConfig
from app.ingestion.parser_registry import ParserRegistry, ParserRegistryError
from app.ingestion.pdf_extractor import PyMuPdfExtractor


def test_registry_registers_builtin_and_known_unavailable_capabilities() -> None:
    registry = ParserRegistry()

    assert registry.capability("pymupdf").status == "available"
    assert registry.capability("docling").status == "unavailable"
    assert registry.capability("ragflow_deepdoc").status == "unavailable"
    unknown = registry.capability("not_registered")
    assert unknown.parser_id == "not_registered"
    assert unknown.diagnostic is not None
    assert unknown.diagnostic.code == "unknown_parser"


def test_registry_rejects_unavailable_external_parser_without_importing_it() -> None:
    registry = ParserRegistry()
    config = IngestionConfig(
        parser={
            "mode": "docling",
            "experimental_external_parsers_enabled": True,
            "limits": {
                "max_file_bytes": 1,
                "max_pages": 1,
                "max_memory_mb": 1,
                "timeout_seconds": 1.0,
                "max_response_bytes": 1,
                "max_attempts": 1,
                "initial_backoff_seconds": 0.0,
            },
        }
    )

    with pytest.raises(ParserRegistryError, match="parser_unavailable:docling"):
        registry.create(config)


def test_pymupdf_factory_is_lazy(monkeypatch) -> None:
    registry = ParserRegistry()
    called = False

    def factory(_config: IngestionConfig) -> object:
        nonlocal called
        called = True
        return object()

    monkeypatch.setitem(registry._factories, "pymupdf", factory)
    assert called is False
    assert registry.create(IngestionConfig()) is not None
    assert called is True


def test_registry_rejects_duplicate_factory_registration() -> None:
    registry = ParserRegistry()

    with pytest.raises(ValueError, match="already registered"):
        registry.register_factory("pymupdf", lambda _config: object())


def test_auto_experimental_is_behind_explicit_flag() -> None:
    registry = ParserRegistry()
    config = IngestionConfig(
        parser={
            "mode": "auto_experimental",
            "experimental_external_parsers_enabled": True,
            "limits": {
                "max_file_bytes": 1,
                "max_pages": 1,
                "max_memory_mb": 1,
                "timeout_seconds": 1.0,
                "max_response_bytes": 1,
                "max_attempts": 1,
                "initial_backoff_seconds": 0.0,
            },
        }
    )
    with pytest.raises(ParserRegistryError, match="parser_unavailable:auto_experimental"):
        registry.create(config)


def test_registry_pymupdf_adapter_preserves_the_reference_extractor_output(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "reference.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Cider reference extraction\nResults are reproducible.")
    document.save(pdf_path)
    document.close()
    config = IngestionConfig(min_page_text_characters=10, min_text_page_ratio=0.5)

    reference = PyMuPdfExtractor(
        min_page_text_characters=config.min_page_text_characters,
        min_text_page_ratio=config.min_text_page_ratio,
    ).extract(pdf_path)
    adapter = ParserRegistry().create(config)

    assert isinstance(adapter, PyMuPdfExtractor)
    assert adapter.extract(pdf_path).to_dict() == reference.to_dict()
