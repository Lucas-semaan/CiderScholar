from __future__ import annotations

from pathlib import Path

import pytest

from app.updates.checkpoints import append_jsonl_object, read_jsonl_objects


def test_journal_streams_rows_and_recovers_after_malformed_lines(tmp_path, monkeypatch) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text(
        '{"id": 1}\n\ninvalid\n[]\nnull\n42\n{"incomplete":\n{"id": 2}\n',
        encoding="utf-8",
    )
    append_jsonl_object(path, {"id": 3, "title": "Cidre fermenté"})

    def forbid_whole_file_read(*args, **kwargs):
        raise AssertionError("journal must be read one line at a time")

    monkeypatch.setattr(Path, "read_text", forbid_whole_file_read)
    assert read_jsonl_objects(path) == [
        {"id": 1},
        {"id": 2},
        {"id": 3, "title": "Cidre fermenté"},
    ]
    assert read_jsonl_objects(tmp_path / "missing.jsonl") == []


def test_journal_does_not_hide_io_or_encoding_failures(tmp_path) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(UnicodeDecodeError):
        read_jsonl_objects(path)
    with pytest.raises(OSError):
        append_jsonl_object(tmp_path / "missing" / "journal.jsonl", {"id": 1})
