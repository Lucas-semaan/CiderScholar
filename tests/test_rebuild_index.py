from __future__ import annotations

import json
from types import SimpleNamespace

from scripts import rebuild_index


class _VerificationIndex:
    def __init__(self, settings) -> None:
        self.collection_name = settings.qdrant.collection_name
        self.path = settings.paths.qdrant_dir
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_verify_generation_does_not_load_an_embedding_backend(
    settings,
    monkeypatch,
    capsys,
) -> None:
    expected = SimpleNamespace(
        generation_id="00000000-0000-0000-0000-000000000001",
        state="ready",
        indexed_article_count=2,
        indexed_chunk_count=5,
        qdrant_point_count=5,
        fully_indexed=True,
    )
    monkeypatch.setattr(rebuild_index, "load_settings", lambda _path: settings)
    monkeypatch.setattr(rebuild_index, "settings_for_corpus", lambda configured, _scope: configured)
    monkeypatch.setattr(rebuild_index, "QdrantLocalIndex", _VerificationIndex)
    monkeypatch.setattr(
        rebuild_index,
        "verify_index_generation_snapshot",
        lambda _database, _index: expected,
    )
    monkeypatch.setattr(
        rebuild_index,
        "SentenceTransformerBackend",
        lambda _settings: (_ for _ in ()).throw(AssertionError("backend must stay unloaded")),
    )

    assert rebuild_index.main(["--verify-generation"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "collection_name": settings.qdrant.collection_name,
        "generation_id": expected.generation_id,
        "state": "ready",
        "indexed_article_count": 2,
        "indexed_chunk_count": 5,
        "qdrant_point_count": 5,
        "fully_indexed": True,
        "verified": True,
        "corpus": "common",
    }
