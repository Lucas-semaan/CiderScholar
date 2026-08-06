from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.deep_research.cache import (
    DeepResearchCacheEntry,
    DeepResearchCacheSignature,
    DeepResearchResponseCache,
)


def _signature(**updates) -> DeepResearchCacheSignature:
    values = {
        "question": "Question cidricole",
        "common_corpus_sha256": "a" * 64,
        "models": {"argo": "model-a"},
        "prompts": {"claims": "prompt-a"},
        "parameters": {"top_k": 12},
    }
    values.update(updates)
    return DeepResearchCacheSignature.build(**values)


def test_every_required_dimension_invalidates_cache_key() -> None:
    baseline = _signature()
    variants = [
        _signature(question="Autre question"),
        _signature(common_corpus_sha256="c" * 64),
        _signature(models={"argo": "model-b"}),
        _signature(prompts={"claims": "prompt-b"}),
        _signature(parameters={"top_k": 8}),
    ]

    assert len({baseline.cache_key_sha256, *(item.cache_key_sha256 for item in variants)}) == 6


def test_cache_round_trip_verifies_signature_and_response_hash(settings) -> None:
    cache = DeepResearchResponseCache(settings, settings.paths.cache_dir / "responses")
    signature = _signature()

    written = cache.put(
        signature,
        answer_markdown="Réponse locale.",
        details={"citations": [{"article_id": "article-1"}]},
    )

    assert cache.get(signature) == written
    path = cache._path(signature)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["answer_markdown"] = "Réponse altérée."
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError, match="response hash"):
        cache.get(signature)


def test_cache_signature_rejects_fabricated_key() -> None:
    valid = _signature().model_dump()
    valid["cache_key_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="signed dimensions"):
        DeepResearchCacheSignature.model_validate(valid)


def test_cache_entry_rejects_signature_mismatch(settings) -> None:
    cache = DeepResearchResponseCache(settings, settings.paths.cache_dir / "responses")
    expected = _signature()
    other = _signature(question="Autre question")
    entry = cache.put(
        other,
        answer_markdown="Autre réponse.",
        details={},
    )
    expected_path = cache._path(expected)
    expected_path.parent.mkdir(parents=True)
    expected_path.write_text(entry.model_dump_json(), encoding="utf-8")

    with pytest.raises(RuntimeError, match="signature does not match"):
        cache.get(expected)

    assert isinstance(entry, DeepResearchCacheEntry)
