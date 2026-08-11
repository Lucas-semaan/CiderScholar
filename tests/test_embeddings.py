from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.database.sqlite import Database
from app.desktop.model_integrity import ModelIntegrityError, write_model_manifest
from app.ingestion.embeddings import (
    EmbeddedChunkBatch,
    EmbeddingBatchProcessor,
    LocalEmbeddingModelNotFoundError,
    SentenceTransformerBackend,
    model_storage_name,
    prepare_prefixed_texts,
    verify_local_embedding_model,
)
from app.memory import MemoryLimitError


class FakeEmbeddingBackend:
    model_name = "fake/multilingual"
    dimension = 3

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []
        self.closed = False

    def encode_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.batch_sizes.append(len(texts))
        return [[float(len(text)), 1.0, 0.0] for text in texts]

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(len(text)), 0.0, 1.0] for text in texts]

    def close(self) -> None:
        self.closed = True


class RecordingSink:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.batches: list[EmbeddedChunkBatch] = []

    def upsert(self, batch: EmbeddedChunkBatch) -> None:
        if self.fail:
            raise RuntimeError("synthetic vector sink interruption")
        self.batches.append(batch)


class MemoryGuardThatStopsAfterPersistence:
    def check(self, operation: str) -> None:
        if operation == "embedding persistence":
            raise MemoryLimitError("synthetic low-memory condition")


def _seed_chunks(database: Database, count: int = 5) -> None:
    database.save_article_and_chunks(
        {
            "id": "embedding-article",
            "sha256": "e" * 64,
            "doi": None,
            "title": "Synthetic multilingual embedding article",
            "authors": [],
            "pdf_path": "data/pdf/embedding.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": index + 1,
                "page_end": index + 1,
                "chunk_index": index,
                "text": f"Fragment scientifique synthétique numéro {index}.",
                "token_count": 6,
            }
            for index in range(count)
        ],
    )


def test_model_storage_name_is_local_and_deterministic() -> None:
    assert model_storage_name("intfloat/multilingual-e5-base") == ("intfloat--multilingual-e5-base")
    with pytest.raises(ValueError):
        model_storage_name("../")


def test_e5_prefixes_are_applied_once() -> None:
    assert prepare_prefixed_texts(["  texte   français  "], "passage: ") == [
        "passage: texte français"
    ]
    assert prepare_prefixed_texts(["query: scientific cider"], "query: ") == [
        "query: scientific cider"
    ]
    with pytest.raises(ValueError, match="empty"):
        prepare_prefixed_texts(["  "], "query: ")


def test_runtime_never_downloads_a_missing_model(settings) -> None:
    backend = SentenceTransformerBackend(settings)
    with pytest.raises(LocalEmbeddingModelNotFoundError, match="--allow-network"):
        backend.encode_queries(["question locale"])


def test_managed_model_verification_detects_missing_or_changed_weights(tmp_path) -> None:
    model_path = tmp_path / "verified-model"
    model_path.mkdir()
    (model_path / "weights.bin").write_bytes(b"original weights")

    with pytest.raises(ModelIntegrityError, match="manifest"):
        verify_local_embedding_model(model_path, "fake/verified", required=True)

    write_model_manifest(model_path, "fake/verified")
    assert verify_local_embedding_model(model_path, "fake/verified", required=True) is True

    (model_path / "weights.bin").write_bytes(b"altered model weights with another length")
    with pytest.raises(ModelIntegrityError, match="hash mismatch"):
        verify_local_embedding_model(model_path, "fake/verified", required=True)


def test_embedding_processor_uses_bounded_batches(settings) -> None:
    settings.embeddings.batch_size = 2
    database = Database(settings.paths.database_path)
    database.initialize()
    _seed_chunks(database, count=5)
    backend = FakeEmbeddingBackend()
    sink = RecordingSink()

    report = EmbeddingBatchProcessor(settings, database, backend).run(sink)

    assert report.chunks_indexed == 5
    assert report.chunks_failed == 0
    assert report.batches_completed == 3
    assert backend.batch_sizes == [2, 2, 1]
    assert backend.closed is True
    assert [len(batch.chunk_ids) for batch in sink.batches] == [2, 2, 1]
    assert all(not hasattr(batch, "texts") for batch in sink.batches)
    assert database.embedding_status_counts() == {"indexed": 5}


def test_failed_sink_marks_only_current_batch_failed(settings) -> None:
    settings.embeddings.batch_size = 2
    database = Database(settings.paths.database_path)
    database.initialize()
    _seed_chunks(database, count=5)

    report = EmbeddingBatchProcessor(settings, database, FakeEmbeddingBackend()).run(
        RecordingSink(fail=True)
    )

    assert report.chunks_failed == 2
    assert report.error_type == "RuntimeError"
    assert database.embedding_status_counts() == {"failed": 2, "pending": 3}


def test_memory_limit_after_upsert_keeps_the_durable_batch_indexed(settings) -> None:
    settings.embeddings.batch_size = 2
    database = Database(settings.paths.database_path)
    database.initialize()
    _seed_chunks(database, count=3)
    sink = RecordingSink()
    processor = EmbeddingBatchProcessor(settings, database, FakeEmbeddingBackend())
    processor.memory = MemoryGuardThatStopsAfterPersistence()  # type: ignore[assignment]

    report = processor.run(sink)

    assert report.chunks_indexed == 2
    assert report.chunks_failed == 0
    assert report.batches_completed == 1
    assert report.error_type == "MemoryLimitError"
    assert database.embedding_status_counts() == {"indexed": 2, "pending": 1}
    assert [len(batch.chunk_ids) for batch in sink.batches] == [2]


def test_interrupted_processing_state_is_recovered(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _seed_chunks(database, count=1)
    row = database.chunks_for_embedding(limit=1)[0]
    database.update_embedding_status([int(row["id"])], "processing")

    report = EmbeddingBatchProcessor(settings, database, FakeEmbeddingBackend()).run(
        RecordingSink()
    )

    assert report.recovered_processing_chunks == 1
    assert report.chunks_indexed == 1
    assert database.embedding_status_counts() == {"indexed": 1}


def test_embedding_processor_can_target_one_article(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _seed_chunks(database, count=2)
    database.save_article_and_chunks(
        {
            "id": "other-article",
            "sha256": "o" * 64,
            "doi": None,
            "title": "Other synthetic article",
            "authors": [],
            "pdf_path": "data/pdf/other.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "A different pending fragment.",
                "token_count": 4,
            }
        ],
    )
    sink = RecordingSink()

    report = EmbeddingBatchProcessor(settings, database, FakeEmbeddingBackend()).run(
        sink, article_ids=["embedding-article"]
    )

    assert report.chunks_indexed == 2
    assert {article for batch in sink.batches for article in batch.article_ids} == {
        "embedding-article"
    }
    assert database.embedding_status_counts() == {"indexed": 2, "pending": 1}
