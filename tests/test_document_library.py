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
                "text": "Malolactic fermentation evidence found only inside the hard cider PDF.",
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
                    None,
                    "a" * 64,
                    "indexed",
                ),
                (
                    "notice-only",
                    "doi:10.1000/notice-only",
                    "10.1000/notice-only",
                    "Abstract-only study",
                    "Keeving keyword present only in this cidre abstract.",
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
                (
                    "accepted-metadata-only",
                    "doi:10.1000/accepted-metadata-only",
                    "10.1000/accepted-metadata-only",
                    "Accepted lead awaiting content",
                    None,
                    "d" * 64,
                    "not_applicable",
                ),
                (
                    "review-metadata-only",
                    "doi:10.1000/review-metadata-only",
                    "10.1000/review-metadata-only",
                    "Review lead awaiting content",
                    None,
                    "e" * 64,
                    "not_applicable",
                ),
                (
                    "review-abstract",
                    "doi:10.1000/review-abstract",
                    "10.1000/review-abstract",
                    "Review abstract remains a document",
                    "Review abstract content.",
                    "f" * 64,
                    "not_applicable",
                ),
                (
                    "rejected-abstract",
                    "doi:10.1000/rejected-abstract",
                    "10.1000/rejected-abstract",
                    "Rejected abstract remains a document",
                    "Rejected abstract content.",
                    "0" * 64,
                    "not_applicable",
                ),
            ],
        )
        connection.executemany(
            """
            UPDATE bibliographic_records
            SET relevance_status = ?
            WHERE id = ?
            """,
            [
                ("review", "review-metadata-only"),
                ("review", "review-abstract"),
                ("rejected", "rejected-abstract"),
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
                ("accepted-metadata-only", "W4"),
                ("review-metadata-only", "W5"),
                ("review-abstract", "W6"),
                ("rejected-abstract", "W7"),
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
        "documents": 5,
        "full_texts": 1,
        "abstract_only": 4,
        "acquisition_notices": 2,
        "accepted_without_content": 1,
        "review_without_content": 1,
    }
    assert summary["filters"]["themes"] == ["cidre", "fermentation"]


def test_cidre_theme_is_transversal_across_metadata_and_full_text(settings) -> None:
    database = _seed_unified_documents(settings)

    result = browse_document_library(database, theme="cidre", availability="all")

    assert result["total"] == 2
    assert {record["document_type"] for record in result["records"]} == {
        "abstract_only",
        "full_text",
    }
    assert all("cidre" in record["themes"] for record in result["records"])
    assert all(len(record["themes"]) <= 3 for record in result["records"])
    assert all(record["relevance_theme"] == "fermentation" for record in result["records"])


def test_acquisition_queue_keeps_metadata_separate_from_usable_documents(settings) -> None:
    database = _seed_unified_documents(settings)

    queue = browse_document_library(database, availability="metadata_only")
    accepted = browse_document_library(
        database,
        statuses=["accepted"],
        availability="metadata_only",
    )
    review = browse_document_library(
        database,
        statuses=["review"],
        availability="metadata_only",
    )

    assert queue["total"] == 2
    assert {record["document_type"] for record in queue["records"]} == {"metadata_only"}
    assert {record["id"] for record in queue["records"]} == {
        "accepted-metadata-only",
        "review-metadata-only",
    }
    assert [record["id"] for record in accepted["records"]] == ["accepted-metadata-only"]
    assert [record["id"] for record in review["records"]] == ["review-metadata-only"]
    assert all(record["abstract"] is None for record in queue["records"])
    assert all(record["article_id"] is None for record in queue["records"])


def test_content_type_does_not_depend_on_relevance_or_verified_doi(settings) -> None:
    database = _seed_unified_documents(settings)

    abstracts = browse_document_library(database, availability="abstract_only")

    by_id = {record["id"]: record for record in abstracts["records"]}
    assert by_id["review-abstract"]["relevance_status"] == "review"
    assert by_id["rejected-abstract"]["relevance_status"] == "rejected"
    assert by_id["invalid-doi-abstract"]["doi"] is None
    assert set(by_id) == {
        "notice-only",
        "invalid-doi-abstract",
        "review-abstract",
        "rejected-abstract",
    }


def test_document_snapshot_is_invalidated_after_a_database_write(settings) -> None:
    database = _seed_unified_documents(settings)
    before = document_library_summary(database)

    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO bibliographic_records (
                id, canonical_key, doi, title, abstract, authors, content_hash,
                embedding_status, relevance_status
            ) VALUES (
                'new-review-abstract', 'doi:10.1000/new-review-abstract',
                '10.1000/new-review-abstract', 'New review abstract',
                'New persisted content.', '[]', ?, 'not_applicable', 'review'
            )
            """,
            ("9" * 64,),
        )

    after = document_library_summary(database)

    assert after["statistics"]["abstract_only"] == (before["statistics"]["abstract_only"] + 1)


def test_library_api_uses_common_base_and_opens_selected_pdf(settings) -> None:
    _seed_unified_documents(settings)

    with TestClient(create_app(settings)) as client:
        abstract = client.get("/api/library/records", params={"query": "keeving"})
        abstract_only = client.get("/api/library/records", params={"availability": "abstract_only"})
        metadata_only = client.get(
            "/api/library/records",
            params={"availability": "metadata_only", "statuses": "review"},
        )
        full_texts = client.get("/api/library/records", params={"availability": "full_text"})
        invalid_doi = client.get("/api/library/records", params={"query": "pomologyinvalid"})
        pdf = client.get("/api/corpus/full-text-1/pdf")

    assert abstract.json()["records"][0]["document_type"] == "abstract_only"
    assert abstract.json()["records"][0]["doi"] == "10.1000/notice-only"
    assert {record["document_type"] for record in abstract_only.json()["records"]} == {
        "abstract_only"
    }
    assert abstract_only.json()["total"] == 4
    assert [record["id"] for record in metadata_only.json()["records"]] == ["review-metadata-only"]
    assert [record["article_id"] for record in full_texts.json()["records"]] == ["full-text-1"]
    assert invalid_doi.json()["records"][0]["id"] == "invalid-doi-abstract"
    assert invalid_doi.json()["records"][0]["doi"] is None
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-1.7")
