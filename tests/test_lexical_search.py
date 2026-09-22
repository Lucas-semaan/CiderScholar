from __future__ import annotations

import pytest

from app.database.sqlite import Database
from app.retrieval.lexical_search import LexicalQueryBuilder, LexicalSearchService


def _add_article(
    database: Database,
    *,
    article_id: str,
    sha_character: str,
    title: str,
    status: str,
    chunks: list[tuple[str, int, str]],
) -> None:
    database.save_article_and_chunks(
        {
            "id": article_id,
            "sha256": sha_character * 64,
            "doi": None,
            "title": title,
            "authors": [],
            "publication_year": 2024,
            "pdf_path": f"data/pdf/{article_id}.pdf",
            "validation_status": status,
            "source": "local",
        },
        [
            {
                "section": section,
                "page_start": page,
                "page_end": page,
                "chunk_index": index,
                "text": text,
                "token_count": len(text.split()),
            }
            for index, (section, page, text) in enumerate(chunks)
        ],
    )


def _service(settings) -> LexicalSearchService:
    database = Database(settings.paths.database_path)
    database.initialize()
    _add_article(
        database,
        article_id="polyphenol-article",
        sha_character="p",
        title="Stabilité des polyphénols pendant le stockage",
        status="validated",
        chunks=[
            (
                "Results",
                3,
                "Les polyphénols diminuent progressivement pendant le stockage prolongé.",
            ),
            (
                "Discussion",
                4,
                "La température influence la fermentation et la stabilité aromatique.",
            ),
        ],
    )
    _add_article(
        database,
        article_id="sensor-article",
        sha_character="s",
        title="Temperature calibration for a galactic sensor",
        status="indexed",
        chunks=[
            (
                "Materials and methods",
                2,
                "The telescope sensor temperature was calibrated before observation.",
            )
        ],
    )
    _add_article(
        database,
        article_id="hidden-article",
        sha_character="h",
        title="Unvalidated hidden discovery",
        status="awaiting_validation",
        chunks=[("Results", 1, "Polyphénols polyphénols polyphénols stockage.")],
    )
    return LexicalSearchService(settings, database)


def test_query_builder_neutralizes_fts_operators(settings) -> None:
    prepared = LexicalQueryBuilder(settings).build('polyphenols" OR *')
    assert prepared.terms == ["polyphenols"]
    assert prepared.fts5_expression == '"polyphenols"*'


def test_query_builder_supports_all_phrase_and_prefix_modes(settings) -> None:
    builder = LexicalQueryBuilder(settings)
    assert builder.build("temperature fermentation", "all").fts5_expression == (
        '"temperature"* AND "fermentation"*'
    )
    assert builder.build("effect of temperature", "phrase").fts5_expression == (
        '"effect of temperature"'
    )
    assert builder.build("the and de la").fts5_expression == ""


def test_query_builder_can_disable_prefix_matching_for_a_first_retrieval_wave(settings) -> None:
    builder = LexicalQueryBuilder(settings)

    assert builder.build("temperature").fts5_expression == '"temperature"*'
    assert builder.build("temperature", prefix_matching=False).fts5_expression == '"temperature"'


def test_search_is_accent_insensitive_and_page_traceable(settings) -> None:
    response = _service(settings).search(
        "Comment les polyphenols évoluent-ils pendant le stockage ?", limit=10
    )
    assert response.results
    first = response.results[0]
    assert first.article_id == "polyphenol-article"
    assert first.article_title == "Stabilité des polyphénols pendant le stockage"
    assert first.section == "Results"
    assert (first.page_start, first.page_end) == (3, 3)
    assert first.relevance_score >= 0
    assert all(result.article_id != "hidden-article" for result in response.results)


def test_search_prefers_a_structural_locator_to_legacy_chunk_pages(settings) -> None:
    service = _service(settings)
    database = service.database
    chunk_id = int(database.chunks_for_article("polyphenol-article", limit=1)[0]["id"])
    asset_id = database.save_article_source_asset(
        article_id="polyphenol-article",
        kind="jats_xml",
        file_path="data/native/polyphenols.xml",
        sha256="e" * 64,
        media_type="application/xml",
        byte_count=100,
        provider="europe_pmc",
    )
    database.save_structural_chunk_locator(
        chunk_id=chunk_id,
        asset_id=asset_id,
        section_path="Results / Storage",
        paragraph_start=2,
        paragraph_end=2,
        xml_id_start="storage-2",
        xml_id_end="storage-2",
        span_text="Les polyphénols diminuent progressivement pendant le stockage prolongé.",
    )

    result = service.search("polyphénols stockage", limit=10).results[0]

    assert result.locator_kind == "structural"
    assert result.page_start is None
    assert result.page_end is None
    assert result.section_path == "Results / Storage"
    assert (result.paragraph_start, result.paragraph_end) == (2, 2)


def test_search_filters_by_article_and_section(settings) -> None:
    service = _service(settings)
    filtered_article = service.search(
        "temperature",
        article_ids=["sensor-article"],
    )
    assert [result.article_id for result in filtered_article.results] == ["sensor-article"]

    filtered_section = service.search(
        "temperature",
        sections=["Discussion"],
    )
    assert [result.article_id for result in filtered_section.results] == ["polyphenol-article"]
    assert service.search("temperature", article_ids=[]).results == []
    assert service.search("temperature", sections=[]).results == []


def test_prefix_all_terms_and_limit_are_enforced(settings) -> None:
    service = _service(settings)
    response = service.search("température ferment", mode="all", limit=1)
    assert len(response.results) == 1
    assert response.results[0].article_id == "polyphenol-article"
    assert response.results[0].rank == 1


def test_meaningless_or_empty_question_returns_no_match(settings) -> None:
    service = _service(settings)
    assert service.search("the and de la").results == []
    assert service.search("   ").results == []
    with pytest.raises(ValueError, match="between 1 and 1000"):
        service.search("temperature", limit=0)
    with pytest.raises(ValueError, match="character limit"):
        service.search("x" * 2001)


def test_read_session_reuses_bounded_readonly_connection_and_preserves_results(settings) -> None:
    service = _service(settings)
    expected = service.search("température").results

    session = service.read_session()
    with session:
        actual = session.search("température").results
        connection = session._session.connection  # noqa: SLF001 - verifies SQLite session contract.
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        assert connection.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert connection.execute("PRAGMA cache_size").fetchone()[0] == -(16 * 1024)

    assert actual == expected
    with pytest.raises(RuntimeError, match="not active"):
        session.search("température")


def test_read_session_skips_empty_caption_match_then_detects_new_caption(settings) -> None:
    service = _service(settings)
    database = service.database
    statements: list[str] = []

    with service.read_session() as session:
        connection = session._session.connection  # noqa: SLF001 - trace only the session connection.
        connection.set_trace_callback(statements.append)
        assert session.search("caption-inexistante").results == []
        assert not any(
            "document_element_captions_fts MATCH" in statement for statement in statements
        )

        with database.transaction() as writer:
            chunk_id = writer.execute(
                "SELECT id FROM chunks WHERE article_id = ? LIMIT 1", ("polyphenol-article",)
            ).fetchone()[0]
            writer.execute(
                """
                INSERT INTO document_elements (
                    id, article_id, local_element_id, kind, page_number, bbox_json,
                    source_kind, synthetic_caption
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "caption-element",
                    "polyphenol-article",
                    "figure-1",
                    "figure",
                    3,
                    "[0, 0, 1, 1]",
                    "pdf_embedded",
                    "marqueur-caption-unique",
                ),
            )
            writer.execute(
                """
                INSERT INTO document_element_relations (
                    element_id, relation, page_number, related_chunk_id, source_excerpt,
                    source_excerpt_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("caption-element", "nearest_page_text", 3, chunk_id, "source", "a" * 64),
            )

        result = session.search("marqueur-caption-unique").results

    assert [item.article_id for item in result] == ["polyphenol-article"]
