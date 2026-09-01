from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict

from app.retrieval.retrieval_cache import RetrievalCacheSignature, RetrievalResultCache


def _signature(**updates: object) -> RetrievalCacheSignature:
    values: dict[str, object] = {
        "query": "  Quels levures   pour le cidre ? ",
        "variants": ["levures cidre", "cider yeast"],
        "corpus_fingerprint": "a" * 64,
        "scope": "common",
        "retrieval_config": {"hybrid": True},
        "embedding": {"model": "e5", "manifest_sha256": "b" * 64},
        "reranker": {"model": "bge", "manifest_sha256": "c" * 64},
        "filters_limits": {"top_k": 40, "year_from": 2000},
    }
    values.update(updates)
    return RetrievalCacheSignature.build(**values)  # type: ignore[arg-type]


def test_signature_contains_all_retrieval_dimensions() -> None:
    baseline = _signature()
    variants = [
        _signature(query="Autre question"),
        _signature(variants=["autre variante"]),
        _signature(corpus_fingerprint="d" * 64),
        _signature(scope="private"),
        _signature(retrieval_config={"hybrid": False}),
        _signature(embedding={"model": "other", "manifest_sha256": "b" * 64}),
        _signature(reranker={"model": "other", "manifest_sha256": "c" * 64}),
        _signature(filters_limits={"top_k": 60}),
    ]

    assert len({baseline.cache_key_sha256, *(item.cache_key_sha256 for item in variants)}) == 9


def test_whitespace_normalization_does_not_split_exact_cache_keys() -> None:
    assert (
        _signature().cache_key_sha256
        == _signature(
            query="Quels levures pour le cidre ?", variants=[" levures  cidre ", "cider yeast"]
        ).cache_key_sha256
    )


def test_previous_retrieval_implementation_cache_is_rejected(tmp_path) -> None:
    stale = _signature(schema_version=1)
    cache = RetrievalResultCache(tmp_path / "retrieval")

    with pytest.raises(ValueError, match="schema version is stale"):
        cache.put(stale, {"article_ids": ["stale"]})


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_ids: list[str]


def test_round_trip_is_atomic_json_and_can_be_typed(tmp_path) -> None:
    cache = RetrievalResultCache(tmp_path / "retrieval")
    signature = _signature()

    written = cache.put(signature, {"article_ids": ["article-1"]})

    assert cache.get(signature) == {"article_ids": ["article-1"]}
    assert cache.get_typed(signature, _Result) == _Result(article_ids=["article-1"])
    assert written.validates_integrity()


def test_corrupt_or_mismatched_entries_are_safe_misses(tmp_path) -> None:
    cache = RetrievalResultCache(tmp_path / "retrieval")
    signature = _signature()
    cache.put(signature, {"article_ids": ["article-1"]})
    path = cache._path(signature)

    path.write_text("{not json", encoding="utf-8")
    assert cache.get(signature) is None

    cache.put(signature, {"article_ids": ["article-1"]})
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["result"] = {"article_ids": ["altered"]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert cache.get(signature) is None


def test_stale_corpus_entries_can_be_explicitly_invalidated(tmp_path) -> None:
    cache = RetrievalResultCache(tmp_path / "retrieval")
    current = _signature(corpus_fingerprint="e" * 64)
    stale = _signature(corpus_fingerprint="f" * 64)
    cache.put(current, {"article_ids": ["current"]})
    cache.put(stale, {"article_ids": ["stale"]})

    assert cache.invalidate_stale("e" * 64) == 1
    assert cache.get(current) == {"article_ids": ["current"]}
    assert cache.get(stale) is None


def test_closed_cache_rejects_new_access(tmp_path) -> None:
    cache = RetrievalResultCache(tmp_path / "retrieval")
    cache.close()

    with pytest.raises(RuntimeError, match="closed"):
        cache.get(_signature())
