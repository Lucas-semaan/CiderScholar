import pytest

from app.database.sqlite import Database
from app.models.evidence import ArticleEvidence, Finding


def test_structural_finding_requires_structural_bounds_without_fake_pages() -> None:
    finding = Finding(
        claim="Native result.",
        source_excerpt="Native result.",
        chunk_id="1",
        locator_kind="structural",
        section_path="Results",
        paragraph_start=2,
        paragraph_end=2,
        xml_id_start="result-2",
        xml_id_end="result-2",
    )

    assert finding.page_start is None
    with pytest.raises(ValueError, match="cannot carry page"):
        finding.model_copy(update={"page_start": 1}).validate_pages()


def test_page_finding_remains_backward_compatible() -> None:
    finding = Finding(
        claim="PDF result.", source_excerpt="PDF result.", chunk_id="2", page_start=3, page_end=3
    )

    assert finding.locator_kind == "page"


def test_structural_evidence_is_persisted_and_rehydrated(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        {"id": "native", "sha256": "a" * 64, "title": "Native", "pdf_path": ""},
        [
            {
                "chunk_index": 0,
                "text": "Native evidence.",
                "token_count": 2,
                "page_start": None,
                "page_end": None,
            }
        ],
    )
    asset_id = database.save_article_source_asset(
        article_id="native",
        kind="jats_xml",
        file_path="native.xml",
        sha256="b" * 64,
        media_type="application/xml",
        byte_count=1,
    )
    chunk_id = int(database.chunks_for_article("native", limit=1)[0]["id"])
    database.save_structural_chunk_locator(
        chunk_id=chunk_id,
        asset_id=asset_id,
        section_path="Results",
        paragraph_start=1,
        paragraph_end=1,
        xml_id_start="p1",
        xml_id_end="p1",
        span_text="Native evidence.",
    )
    database.create_query(
        query_id="query",
        original_query="native",
        expanded_queries=[],
        selected_article_ids=["native"],
    )
    database.start_article_evidence_run(
        query_id="query", article_id="native", selected_chunk_ids=[chunk_id]
    )
    database.save_article_evidence(
        query_id="query",
        selected_chunk_ids=[chunk_id],
        evidence=ArticleEvidence(
            article_id="native",
            relevance_score=1,
            question_addressed="native",
            findings=[
                Finding(
                    claim="Native evidence.",
                    source_excerpt="Native evidence.",
                    chunk_id=str(chunk_id),
                    locator_kind="structural",
                    section_path="Results",
                    paragraph_start=1,
                    paragraph_end=1,
                    xml_id_start="p1",
                    xml_id_end="p1",
                )
            ],
            topics=[],
            contradictions=[],
            missing_information=[],
        ),
    )
    restored = database.load_article_evidence("query", "native")
    assert restored is not None
    assert restored.findings[0].section_path == "Results"
    assert restored.findings[0].page_start is None
