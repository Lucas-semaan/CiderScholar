from __future__ import annotations

from pathlib import Path

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers, processors

from app.ingestion.chunker import ScientificChunker
from app.ingestion.pdf_extractor import PageText
from app.ingestion.token_budget import LocalEmbeddingTokenBudget


def _budget(tmp_path: Path, *, maximum: int = 12) -> LocalEmbeddingTokenBudget:
    vocabulary = {
        "[UNK]": 0,
        "<s>": 1,
        "</s>": 2,
        "passage": 3,
        ":": 4,
    }
    tokenizer = Tokenizer(models.WordLevel(vocabulary, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="<s> $A </s>",
        special_tokens=[("<s>", 1), ("</s>", 2)],
    )
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))
    return LocalEmbeddingTokenBudget(path, prefix="passage: ", max_tokens=maximum)


def test_exact_budget_counts_prefix_and_special_tokens(tmp_path: Path) -> None:
    budget = _budget(tmp_path)

    assert budget.count("apple cider") == 6
    with pytest.raises(ValueError, match="model limit"):
        list(budget.split("apple cider", 13))


def test_exact_budget_splits_without_losing_scientific_text(tmp_path: Path) -> None:
    budget = _budget(tmp_path, maximum=10)
    text = " ".join(f"measurement{index}" for index in range(25))

    parts = list(budget.split(text, 10))

    assert len(parts) > 1
    assert " ".join(parts).split() == text.split()
    assert all(budget.count(part) <= 10 for part in parts)


def test_exact_chunking_keeps_major_sections_as_hard_boundaries(tmp_path: Path) -> None:
    budget = _budget(tmp_path, maximum=12)
    pages = [
        PageText(1, "Abstract\n" + " ".join(f"abstract{index}" for index in range(18))),
        PageText(2, "Results\n" + " ".join(f"result{index}" for index in range(18))),
        PageText(3, "Conclusion\n" + " ".join(f"conclusion{index}" for index in range(18))),
    ]

    chunks = ScientificChunker(
        target_tokens=9,
        max_tokens=12,
        overlap_tokens=5,
        token_budget=budget,
    ).chunk(pages)

    assert {chunk.section for chunk in chunks} == {"Abstract", "Results", "Conclusion"}
    assert all(chunk.page_start == chunk.page_end for chunk in chunks)
    assert all(chunk.token_count == budget.count(chunk.text) <= 12 for chunk in chunks)
