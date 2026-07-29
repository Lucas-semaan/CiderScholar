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
