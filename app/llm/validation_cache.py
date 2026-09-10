"""Exact, versioned validation checkpoints; no source text is stored in keys."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError


def content_key(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def client_identity(client: object) -> dict[str, str]:
    return {
        "model": str(getattr(client, "model", "unknown")),
        "provider": str(getattr(client, "provider", type(client).__name__)),
        "endpoint": str(getattr(client, "base_url", "")),
    }


class ValidationCache:
    """Reuse only complete, schema-valid responses to exactly identical inputs."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root
        self.memory: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get[T: BaseModel](self, key: str, schema: type[T]) -> T | None:
        try:
            entry = self.memory.get(key)
            if entry is None and self.root is not None:
                entry = json.loads((self.root / f"{key}.json").read_text(encoding="utf-8"))
            if entry is None or entry["key"] != key:
                raise ValueError("missing checkpoint")
            if content_key(entry["result"]) != entry["sha256"]:
                raise ValueError("corrupt checkpoint")
            result = schema.model_validate(entry["result"])
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            self.misses += 1
            return None
        self.hits += 1
        return result

    def put(self, key: str, result: BaseModel) -> None:
        value = result.model_dump(mode="json")
        entry = {"key": key, "result": value, "sha256": content_key(value)}
        self.memory[key] = entry
        if self.root is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{key}.json"
        temporary = self.root / f".{key}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(entry, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
