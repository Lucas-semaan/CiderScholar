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
