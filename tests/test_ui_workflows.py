from __future__ import annotations

from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from app.ingestion.pipeline import IngestionReport
from app.models.synthesis import BibliographyEntry
from app.services.workflows import (
    apply_runtime_overrides,
    bibliography_to_bibtex,
    ingest_paths,
    pdf_paths,
    save_uploaded_pdf,
)


def test_uploaded_pdf_is_sanitized_hashed_and_confined(settings) -> None:
    destination = save_uploaded_pdf(
        settings,
        original_name="../étude locale.pdf",
        stream=BytesIO(b"%PDF-1.7\nsynthetic"),
    )

    assert destination.read_bytes() == b"%PDF-1.7\nsynthetic"
    assert destination.suffix == ".pdf"
    assert destination.parent == settings.paths.pdf_dir / "uploads"
    assert ".." not in destination.name

    with pytest.raises(ValueError, match=".pdf"):
        save_uploaded_pdf(settings, original_name="notes.txt", stream=BytesIO(b"not a pdf"))


def test_pdf_folder_discovery_is_explicit_and_recursive(settings) -> None:
    root = settings.paths.pdf_dir
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "one.pdf").write_bytes(b"one")
    (nested / "two.pdf").write_bytes(b"two")
    (nested / "three.PDF").write_bytes(b"three")
    (nested / "ignored.txt").write_text("ignored", encoding="utf-8")

    assert [path.name for path in pdf_paths(root, recursive=False)] == ["one.pdf"]
    assert [path.name for path in pdf_paths(root, recursive=True)] == [
        "three.PDF",
        "two.pdf",
        "one.pdf",
    ]


def test_ingest_paths_runs_explicit_ocr_fallback(settings, tmp_path) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"synthetic scan")
    native_pipeline = Mock()
    native_pipeline.ingest_file.return_value = IngestionReport(
        pdf_path=str(pdf),
        status="ocr_required",
        duration_seconds=0.0,
    )
    ocr_pipeline = Mock()
    ocr_pipeline.ingest_file.return_value = IngestionReport(
        pdf_path=str(pdf),
        status="chunks_ready",
        duration_seconds=0.0,
    )

    with patch(
        "app.services.workflows.IngestionPipeline",
        side_effect=[native_pipeline, ocr_pipeline],
    ) as pipeline_factory:
        reports = ingest_paths(
            settings,
            Mock(),
            [pdf],
            ocr_extractor=Mock(),
        )

    assert reports[0].status == "chunks_ready"
    assert pipeline_factory.call_count == 2
    ocr_pipeline.ingest_file.assert_called_once_with(pdf)


def test_ingest_paths_waits_and_retries_memory_pressure(settings, tmp_path) -> None:
    pdf = tmp_path / "large.pdf"
    pdf.write_bytes(b"synthetic PDF")
    pipeline = Mock()
    pipeline.ingest_file.side_effect = [
        IngestionReport(
            pdf_path=str(pdf),
            status="failed",
            error_type="MemoryLimitError",
            error_message="temporary pressure",
            duration_seconds=0.0,
        ),
        IngestionReport(
            pdf_path=str(pdf),
            status="chunks_ready",
            duration_seconds=0.0,
        ),
    ]

    with (
        patch("app.services.workflows.IngestionPipeline", return_value=pipeline),
        patch("app.services.workflows.sleep") as wait,
    ):
        reports = ingest_paths(
            settings,
            Mock(),
            [pdf],
            memory_retry_attempts=2,
            memory_retry_delay_seconds=0.01,
        )

    assert reports[0].status == "chunks_ready"
    wait.assert_called_once_with(0.01)
    assert pipeline.ingest_file.call_count == 2


def test_runtime_overrides_are_validated_without_mutating_base(settings) -> None:
    updated = apply_runtime_overrides(
        settings,
        {
            "argo": {"model": "alternate-argo-model"},
            "retrieval": {
                "lexical_weight": 0.4,
                "vector_weight": 0.4,
                "reranker_weight": 0.2,
            },
        },
    )

    assert updated.argo.model == "alternate-argo-model"
    assert settings.argo.model == "chat-gpt-oss-120b"
    with pytest.raises(ValidationError, match="weights must add up"):
        apply_runtime_overrides(
            settings,
            {"retrieval": {"lexical_weight": 0.9}},
        )


def test_bibtex_uses_only_structured_bibliography_metadata() -> None:
    rendered = bibliography_to_bibtex(
        [
            BibliographyEntry(
                article_id="article/1",
                title="Local {study}",
                authors=["Ada Test", "Jean Exemple"],
                journal="SQLite Journal",
                publication_year=2026,
                doi="10.1000/from-sqlite",
            )
        ]
    )

    assert rendered.startswith("@article{article-1,")
    assert "Ada Test and Jean Exemple" in rendered
    assert "doi = {10.1000/from-sqlite}" in rendered
    assert "Local \\{study\\}" in rendered
    assert "ciderscholar_scope = {common}" in rendered
    assert "note = {Corpus commun}" in rendered


def test_bibtex_exports_every_source_as_the_common_corpus() -> None:
    rendered = bibliography_to_bibtex(
        [
            BibliographyEntry(
                article_id="article-2",
                title="Shared study",
                authors=[],
                journal=None,
                publication_year=None,
                doi=None,
            )
        ]
    )

    assert "ciderscholar_scope = {common}" in rendered
    assert "note = {Corpus commun}" in rendered
