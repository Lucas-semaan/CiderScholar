"""Exact local-tokenizer budgets shared by chunking and embedding guards."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config import Settings


def model_storage_name(model_name: str) -> str:
    """Map a registry identifier to one deterministic local directory name."""

    source = model_name.strip()
    if any(part in {"", ".", ".."} for part in re.split(r"[/\\]", source)):
        raise ValueError("invalid embedding model name")
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "--", source)
    if not normalized or normalized in {".", ".."}:
        raise ValueError("invalid embedding model name")
    return normalized


def local_model_path(settings: Settings, model_name: str | None = None) -> Path:
    selected = model_name or settings.embeddings.model_name
    return settings.paths.models_dir / model_storage_name(selected)


def prepare_prefixed_texts(texts: Sequence[str], prefix: str) -> list[str]:
    """Normalize whitespace and apply an E5 task prefix exactly once."""

    prepared: list[str] = []
    for text in texts:
        normalized = " ".join(text.split()).strip()
        if not normalized:
            raise ValueError("cannot embed empty text")
        prepared.append(normalized if normalized.startswith(prefix) else f"{prefix}{normalized}")
    return prepared


class LocalEmbeddingTokenBudget:
    """Count and split text with the exact bundled tokenizer, without network access."""

    def __init__(self, tokenizer_path: str | Path, *, prefix: str, max_tokens: int) -> None:
        path = Path(tokenizer_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"local embedding tokenizer not found: {path}")
        try:
            from tokenizers import Tokenizer
        except ImportError as exc:  # pragma: no cover - installation concern
            raise RuntimeError("tokenizers is required for exact chunk budgets") from exc
        self.path = path
        self.prefix = prefix
        self.max_tokens = max_tokens
        self._tokenizer: Any = Tokenizer.from_file(str(path))
        self._overhead_tokens = len(self._tokenizer.encode(prefix, add_special_tokens=True).ids)
        if self._overhead_tokens >= max_tokens:
            raise ValueError("embedding prefix leaves no token budget for scientific text")

    @classmethod
    def from_settings(cls, settings: Settings) -> LocalEmbeddingTokenBudget:
        return cls(
            local_model_path(settings) / "tokenizer.json",
            prefix=settings.embeddings.passage_prefix,
            max_tokens=settings.embeddings.max_sequence_length,
        )

    def count(self, text: str) -> int:
        prepared = prepare_prefixed_texts([text], self.prefix)[0]
        return len(self._tokenizer.encode(prepared, add_special_tokens=True).ids)

    def split(self, text: str, max_tokens: int) -> Iterator[str]:
        """Yield exact normalized substrings that each fit the complete model input."""

        if max_tokens > self.max_tokens:
            raise ValueError("chunk token limit exceeds the embedding model limit")
        normalized = " ".join(text.split()).strip()
        if not normalized:
            return
        if self.count(normalized) <= max_tokens:
            yield normalized
            return

        content_budget = max_tokens - self._overhead_tokens
        if content_budget <= 0:
            raise ValueError("token limit leaves no room for chunk text")
        encoding = self._tokenizer.encode(normalized, add_special_tokens=False)
        offsets = [offset for offset in encoding.offsets if offset[1] > offset[0]]
        if not offsets:
            raise ValueError("tokenizer produced no offsets for non-empty text")

        start = 0
        while start < len(offsets):
            end = min(start + content_budget, len(offsets))
            minimum = start + max(1, (end - start) // 2)
            if end < len(offsets):
                for boundary in range(end, minimum, -1):
                    gap = normalized[offsets[boundary - 1][1] : offsets[boundary][0]]
                    if any(character.isspace() for character in gap):
                        end = boundary
                        break

            part = normalized[offsets[start][0] : offsets[end - 1][1]].strip()
            while end > start + 1 and self.count(part) > max_tokens:
                end -= 1
                part = normalized[offsets[start][0] : offsets[end - 1][1]].strip()
            if not part or self.count(part) > max_tokens:
                raise ValueError("one tokenizer unit exceeds the configured model budget")
            yield part
            start = end
