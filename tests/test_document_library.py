from __future__ import annotations

from fastapi.testclient import TestClient

from app.database.sqlite import Database
from app.main import create_app
from app.services.document_library import browse_document_library, document_library_summary


def _seed_unified_documents(settings) -> Database:
    database = Database(settings.paths.common_database_path)
    database.initialize()
    pdf = settings.paths.common_pdf_dir / "unified.pdf"
    pdf.write_bytes(b"%PDF-1.7\nunified document")
    database.save_article_and_chunks(
        {
            "id": "full-text-1",
            "sha256": "1" * 64,
            "doi": "10.1000/unified",
            "title": "Full text title",
            "abstract": "PDF abstract",
            "authors": ["Ada Test"],
            "pdf_path": str(pdf),
            "source": "local",
        },
        [
            {
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "Malolactic fermentation evidence found only inside the PDF.",
                "token_count": 8,
                "embedding_status": "indexed",
            }
        ],
    )
    with database.transaction() as connection:
        connection.executemany(
            """
            INSERT INTO bibliographic_records (
                id, canonical_key, doi, title, abstract, authors, content_hash,
                embedding_status, relevance_status, relevance_theme
            ) VALUES (?, ?, ?, ?, ?, '[]', ?, ?, 'accepted', 'fermentation')
            """,
            [
                (
                    "notice-matching-pdf",
                    "doi:10.1000/unified",
                    "10.1000/unified",
                    "Unified bibliographic title",
                    "Reference metadata for the full text.",
                    "a" * 64,
                    "indexed",
                ),
                (
                    "notice-only",
                    "doi:10.1000/notice-only",
                    "10.1000/notice-only",
                    "Abstract-only study",
                    "Keeving keyword present only in this abstract.",
                    "b" * 64,
                    "indexed",
                ),
                (
                    "invalid-doi-abstract",
                    "doi:10.1000/not-bare",
                    "https://doi.org/10.1000/not-bare",
                    "Abstract with an unverified DOI value",
                    "Pomologyinvalid appears only in this excluded abstract.",
                    "c" * 64,
                    "indexed",
                ),
            ],
        )
        connection.executemany(
            """
            INSERT INTO bibliographic_record_sources (record_id, source, source_id)
            VALUES (?, 'OpenAlex', ?)
            """,
            [
                ("notice-matching-pdf", "W1"),
                ("notice-only", "W2"),
                ("invalid-doi-abstract", "W3"),
            ],
        )
    return database


def test_document_library_merges_doi_and_searches_pdf_text(settings) -> None:
    database = _seed_unified_documents(settings)

    result = browse_document_library(database, query="malolactic", availability="all")
    summary = document_library_summary(database)

    assert result["total"] == 1
    assert result["records"][0]["id"] == "notice-matching-pdf"
    assert result["records"][0]["article_id"] == "full-text-1"
    assert result["records"][0]["document_type"] == "full_text"
    assert summary["statistics"] == {
        "documents": 2,
        "full_texts": 1,
        "abstract_only": 1,
        "searchable": 2,
    }


def test_library_api_uses_common_base_and_opens_selected_pdf(settings) -> None:
    _seed_unified_documents(settings)

    with TestClient(create_app(settings)) as client:
        abstract = client.get("/api/library/records", params={"query": "keeving"})
        abstract_only = client.get("/api/library/records", params={"availability": "abstract_only"})
        full_texts = client.get("/api/library/records", params={"availability": "full_text"})
        invalid_doi = client.get("/api/library/records", params={"query": "pomologyinvalid"})
        pdf = client.get("/api/corpus/full-text-1/pdf")

    assert abstract.json()["records"][0]["document_type"] == "abstract_only"
    assert abstract.json()["records"][0]["doi"] == "10.1000/notice-only"
    assert [record["document_type"] for record in abstract_only.json()["records"]] == [
        "abstract_only"
    ]
    assert [record["article_id"] for record in full_texts.json()["records"]] == ["full-text-1"]
    assert invalid_doi.json()["records"] == []
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-1.7")
