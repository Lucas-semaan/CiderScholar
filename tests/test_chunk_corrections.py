from app.database.sqlite import Database


def test_approved_correction_preserves_source_and_invalidates_embedding(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        {"id": "a", "sha256": "a" * 64, "title": "A", "pdf_path": "a.pdf", "source": "local"},
        [
            {
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "Original source.",
                "token_count": 2,
                "embedding_status": "indexed",
            }
        ],
    )
    chunk = database.chunks_for_article("a", limit=1)[0]
    correction_id = database.propose_chunk_correction(
        chunk_id=int(chunk["id"]),
        corrected_text="Curated display.",
        reason="OCR typo",
        reviewer="expert-1",
    )
    database.decide_chunk_correction(correction_id, approved=True)
    with database.connect() as connection:
        correction = connection.execute(
            "SELECT state, original_text_sha256 FROM chunk_corrections"
        ).fetchone()
        source = connection.execute(
            "SELECT text, embedding_status FROM chunks WHERE id = ?", (chunk["id"],)
        ).fetchone()
    assert correction["state"] == "approved"
    assert source["text"] == "Original source."
    assert source["embedding_status"] == "pending"
    assert database.chunks_for_embedding(limit=1)[0]["text"] == "Curated display."
    with database.connect() as connection:
        assert connection.execute("SELECT text FROM chunks_fts").fetchone()[0] == "Curated display."
