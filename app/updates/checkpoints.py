"""Shared JSONL journals for resumable bibliographic discovery commands."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any


def read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    """Read valid object rows, tolerating incomplete journal lines on restart."""

    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            with suppress(json.JSONDecodeError):
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows


def append_jsonl_object(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
