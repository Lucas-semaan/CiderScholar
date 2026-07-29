from pathlib import Path

from app.ingestion.deduplication import sha256_file


def test_sha256_is_streamed_and_stable(tmp_path: Path) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"same synthetic pdf bytes")
    second.write_bytes(b"same synthetic pdf bytes")
    assert sha256_file(first, block_size=3) == sha256_file(second, block_size=7)
