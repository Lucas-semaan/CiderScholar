import pytest

from app.models.figure_evidence import figure_evidence_from_element


def test_figure_evidence_excludes_generated_caption() -> None:
    evidence = figure_evidence_from_element(
        {
            "id": "a:figure",
            "article_id": "a",
            "kind": "figure",
            "page_number": 2,
            "original_caption": "Source caption.",
            "synthetic_caption": "Generated analysis.",
            "text_relations": [{"related_chunk_id": 4}],
        }
    )
    assert evidence.original_caption == "Source caption."
    assert evidence.synthetic_caption is None
    assert evidence.related_chunk_ids == (4,)


def test_figure_evidence_rejects_a_table() -> None:
    with pytest.raises(ValueError, match="source figure"):
        figure_evidence_from_element({"kind": "table"})
