from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.evaluation.extraction_benchmark import ExtractionBenchmarkManifest
from scripts.validate_extraction_benchmark import main


def _entry(index: int, category: str = "text_simple") -> dict[str, object]:
    outcome = (
        "invalid"
        if category == "difficult_invalid"
        else "ocr_required"
        if category == "scanned_ocr"
        else "extracted"
    )
    elements: list[dict[str, object]] = [
        {"id": "element-one", "kind": "text", "reading_order": 1, "section_id": "section-one"}
    ]
    table_cells: list[dict[str, object]] = []
    figures: list[dict[str, object]] = []
    if category == "table_rich":
        elements.append(
            {"id": "element-table", "kind": "table", "reading_order": 2, "section_id": None}
        )
        table_cells.append(
            {"table_element_id": "element-table", "row": 0, "column": 0, "content": "Cellule"}
        )
    if category == "figure_rich":
        elements.append(
            {"id": "element-figure", "kind": "figure", "reading_order": 2, "section_id": None}
        )
        figures.append({"figure_element_id": "element-figure", "expected_caption": "Légende."})
    return {
        "id": f"extract-sample-{index}",
        "category": category,
        "sha256": f"{index:064x}",
        "local_path": "mounted/sample.pdf",
        "redistributable": True,
        "license": "CC-BY-4.0",
        "expected_document_result": {
            "outcome": outcome,
            "page_count": None if outcome == "invalid" else 1,
        },
        "page_annotations": []
        if outcome == "invalid"
        else [
            {
                "page_number": 1,
                "sections": [{"id": "section-one", "title": "One", "level": 1, "reading_order": 1}],
                "elements": elements,
                "expected_text_fragments": [{"reading_order": 1, "text": "Human source text."}],
                "table_cells": table_cells,
                "figures": figures,
                "missing_text_expected": False,
                "duplicated_text_expected": False,
            }
        ],
    }


def _manifest(entries: list[dict[str, object]], *, state: str = "draft") -> dict[str, object]:
    return {
        "schema_version": 1,
        "benchmark_version": "1.0.0",
        "state": state,
        "annotations_human_source_reviewed": state == "final",
        "annotations_frozen_before_candidate_outputs": state == "final",
        "entries": entries,
    }


def _final_entries() -> list[dict[str, object]]:
    categories = (
        "text_simple",
        "multi_column",
        "table_rich",
        "scanned_ocr",
        "figure_rich",
        "difficult_invalid",
    )
    entries = [_entry(index, category) for category in categories for index in range(1, 11)]
    for index, entry in enumerate(entries, start=1):
        entry["id"] = f"extract-sample-{index}"
        entry["sha256"] = f"{index:064x}"
    return entries


def test_final_manifest_requires_complete_human_ground_truth() -> None:
    manifest = ExtractionBenchmarkManifest.model_validate(
        _manifest(_final_entries(), state="final")
    )

    assert len(manifest.entries) == 60


def test_manifest_rejects_duplicate_hashes_and_candidate_labels() -> None:
    first = _entry(1)
    duplicate = _entry(2)
    duplicate["sha256"] = first["sha256"]
    with pytest.raises(ValidationError, match="SHA-256"):
        ExtractionBenchmarkManifest.model_validate(_manifest([first, duplicate]))

    candidate_label = _entry(3)
    candidate_label["candidate_output"] = {"parser": "not a label"}
    with pytest.raises(ValidationError, match="extra"):
        ExtractionBenchmarkManifest.model_validate(_manifest([candidate_label]))


def test_manifest_rejects_path_escape_and_incoherent_text_order() -> None:
    escaped = _entry(1)
    escaped["local_path"] = "../private.pdf"
    with pytest.raises(ValidationError, match="non-traversing"):
        ExtractionBenchmarkManifest.model_validate(_manifest([escaped]))

    invalid_order = _entry(2)
    invalid_order["page_annotations"][0]["expected_text_fragments"][0]["reading_order"] = 2  # type: ignore[index]
    with pytest.raises(ValidationError, match="contiguous"):
        ExtractionBenchmarkManifest.model_validate(_manifest([invalid_order]))


def test_manifest_rejects_cells_outside_tables_and_overlapping_spans() -> None:
    non_table = _entry(1)
    non_table["page_annotations"][0]["table_cells"] = [  # type: ignore[index]
        {"table_element_id": "element-one", "row": 0, "column": 0, "content": "No"}
    ]
    with pytest.raises(ValidationError, match="kind=table"):
        ExtractionBenchmarkManifest.model_validate(_manifest([non_table]))

    overlapping = _entry(2, "table_rich")
    overlapping["page_annotations"][0]["table_cells"].append(  # type: ignore[index]
        {
            "table_element_id": "element-table",
            "row": 0,
            "column": 0,
            "content": "Overlap",
            "column_span": 2,
        }
    )
    with pytest.raises(ValidationError, match="coordinates|overlap"):
        ExtractionBenchmarkManifest.model_validate(_manifest([overlapping]))


def test_manifest_rejects_caption_on_non_figure_and_incomplete_final_labels() -> None:
    non_figure = _entry(1)
    non_figure["page_annotations"][0]["figures"] = [  # type: ignore[index]
        {"figure_element_id": "element-one", "expected_caption": "Not a figure."}
    ]
    with pytest.raises(ValidationError, match="kind=figure"):
        ExtractionBenchmarkManifest.model_validate(_manifest([non_figure]))

    entries = _final_entries()
    entries[0]["page_annotations"][0]["expected_text_fragments"] = []  # type: ignore[index]
    with pytest.raises(ValidationError, match="expected text"):
        ExtractionBenchmarkManifest.model_validate(_manifest(entries, state="final"))


def test_final_manifest_rejects_one_figure_entry_without_its_own_caption() -> None:
    entries = _final_entries()
    entries[49]["page_annotations"][0]["figures"] = []  # type: ignore[index]

    with pytest.raises(ValidationError, match="each final figure_rich entry"):
        ExtractionBenchmarkManifest.model_validate(_manifest(entries, state="final"))


def test_read_only_validator_accepts_draft_without_pdf(tmp_path, capsys) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest([_entry(1)])), encoding="utf-8")

    assert main(["--manifest", str(manifest_path)]) == 0
    assert "valid extraction benchmark manifest" in capsys.readouterr().out
