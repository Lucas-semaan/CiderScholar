from __future__ import annotations

import hashlib
from io import BytesIO

import pytest

from app.file_integrity import HASH_BLOCK_SIZE, sha256_file, sha256_stream


@pytest.mark.parametrize(
    "payload", [b"", b"abc", b"scientific evidence" * 150_000], ids=["empty", "small", "large"]
)
def test_digest_uses_bounded_reads_without_closing_the_callers_stream(payload) -> None:
    class BoundedStream(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= HASH_BLOCK_SIZE
            return super().read(size)

    with BoundedStream(payload) as stream:
        assert sha256_stream(stream) == hashlib.sha256(payload).hexdigest()
        assert not stream.closed


def test_file_digest_accepts_legacy_string_paths_and_custom_block_sizes(tmp_path) -> None:
    path = tmp_path / "document.pdf"
    path.write_bytes(b"verbatim source bytes")

    assert sha256_file(str(path), block_size=3) == sha256_file(path, block_size=7)
    path.unlink()  # The file handle must have been closed, including on Windows.


def test_stream_read_errors_are_not_reported_as_valid_digests() -> None:
    class BrokenStream(BytesIO):
        def read(self, size=-1):
            raise OSError("interrupted read")

    with BrokenStream(b"partial source") as stream, pytest.raises(OSError):
        sha256_stream(stream)
