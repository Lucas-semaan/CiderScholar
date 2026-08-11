from app.ingestion.chunker import ScientificChunker
from app.ingestion.pdf_extractor import PageText


def test_chunker_preserves_pages_sections_and_content_markers() -> None:
    pages = [
        PageText(
            1,
            "Introduction\nPAGE_ONE_MARKER introduces the question. "
            "The context is deliberately detailed. " * 5,
        ),
        PageText(
            2,
            "Results\nPAGE_TWO_MARKER reports a quantitative result. "
            "The observed value was 42 percent. " * 5,
        ),
        PageText(
            3,
            "Discussion\nPAGE_THREE_MARKER discusses the interpretation. "
            "The implications remain local. " * 5,
        ),
    ]
    chunks = ScientificChunker(target_tokens=20, max_tokens=35, overlap_tokens=5).chunk(pages)

    joined = " ".join(chunk.text for chunk in chunks)
    assert "PAGE_ONE_MARKER" in joined
    assert "PAGE_TWO_MARKER" in joined
    assert "PAGE_THREE_MARKER" in joined
    assert {chunk.section for chunk in chunks} >= {"Introduction", "Results", "Discussion"}
    assert all(chunk.page_end - chunk.page_start <= 1 for chunk in chunks)
    assert all(chunk.token_count <= 35 for chunk in chunks)


def test_chunker_never_bridges_nonconsecutive_pages() -> None:
    pages = [PageText(1, "First scientific sentence."), PageText(4, "Distant sentence.")]
    chunks = ScientificChunker(target_tokens=100, max_tokens=120, overlap_tokens=10).chunk(pages)
    assert [(chunk.page_start, chunk.page_end) for chunk in chunks] == [(1, 1), (4, 4)]


def test_chunker_enforces_maximum_on_punctuation_heavy_text() -> None:
    table_like_text = "Results\n" + "Analyte," * 120 + "15.0+/-0.2%;" * 60

    chunks = ScientificChunker(target_tokens=40, max_tokens=50, overlap_tokens=8).chunk(
        [PageText(1, table_like_text)]
    )

    assert len(chunks) > 1
    assert all(chunk.token_count <= 50 for chunk in chunks)
    assert "15.0" in " ".join(chunk.text for chunk in chunks)


def test_chunker_drops_overlap_unit_that_would_break_maximum() -> None:
    long_sentence = " ".join(f"measurement{index}" for index in range(45)) + "."
    following_sentence = "Second result " + " ".join(f"value{index}" for index in range(12)) + "."

    chunks = ScientificChunker(target_tokens=30, max_tokens=50, overlap_tokens=10).chunk(
        [PageText(1, f"Results\n{long_sentence} {following_sentence}")]
    )

    assert len(chunks) == 2
    assert all(chunk.token_count <= 50 for chunk in chunks)
    assert chunks[0].text.startswith("measurement0")
    assert chunks[1].text.startswith("Second result")
