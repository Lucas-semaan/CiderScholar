from __future__ import annotations

import json
from pathlib import Path

from tokenizers import Tokenizer, models, pre_tokenizers, processors

from app.database.sqlite import Database
from app.ingestion.embeddings import local_model_path
from app.ingestion.pdf_extractor import ExtractedDocument, PageText
from scripts.rechunk_corpus import _apply_staging, _create_staging


def _write_tokenizer(path: Path) -> None:
    vocabulary = {"[UNK]": 0, "<s>": 1, "</s>": 2, "passage": 3, ":": 4}
    tokenizer = Tokenizer(models.WordLevel(vocabulary, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="<s> $A </s>",
        special_tokens=[("<s>", 1), ("</s>", 2)],
    )
    path.parent.mkdir(parents=True)
    tokenizer.save(str(path))


def test_rechunk_stages_sections_and_replaces_fts_atomically(settings, tmp_path: Path) -> None:
    settings.ingestion.target_tokens = 100
    settings.ingestion.max_tokens = 120
    settings.ingestion.overlap_tokens = 20
    settings.embeddings.max_sequence_length = 128
    _write_tokenizer(local_model_path(settings) / "tokenizer.json")

    database = Database(settings.paths.database_path)
    database.initialize()
    sha256 = "a" * 64
    database.save_article_and_chunks(
        {
            "id": "article-one",
            "sha256": sha256,
            "title": "Synthetic section-aware study",
            "authors": [],
            "pdf_path": str(tmp_path / "article.pdf"),
            "validation_status": "indexed",
            "source": "test",
        },
        [
            {
                "section": "Other",
                "page_start": 1,
                "page_end": 2,
                "chunk_index": 0,
                "text": "legacy chunk",
                "token_count": 2,
                "embedding_status": "indexed",
            }
        ],
    )
    pages = [
        PageText(1, "Abstract\n" + " ".join(f"abstract{index}" for index in range(180))),
        PageText(2, "Conclusion\n" + " ".join(f"conclusion{index}" for index in range(180))),
    ]
    document = ExtractedDocument(
        pdf_path=str(tmp_path / "article.pdf"),
        page_count=2,
        pages=pages,
        metadata={},
        text_character_count=sum(len(page.text) for page in pages),
        text_page_count=2,
        requires_ocr=False,
    )
    cache = settings.paths.extracted_dir / f"{sha256}.pages.json"
    cache.write_text(json.dumps(document.to_dict()), encoding="utf-8")
    staging = tmp_path / "staging.sqlite3"

    staged = _create_staging(settings, staging)
    applied = _apply_staging(settings, staging)
    chunks = database.chunks_for_article("article-one", limit=20)

    assert staged["max_tokens"] <= 120
    assert applied == {"chunks": len(chunks), "fts": len(chunks), "oversized": 0}
    assert {row["section"] for row in chunks} == {"Abstract", "Conclusion"}
    assert {row["embedding_status"] for row in chunks} == {"pending"}
    assert database.lexical_search("conclusion179")
