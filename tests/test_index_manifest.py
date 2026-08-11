from __future__ import annotations

import json
import sqlite3

import pytest
from qdrant_client import models

from app.database.sqlite import Database
from app.desktop.model_integrity import write_model_manifest
from app.ingestion.embeddings import EmbeddedChunkBatch, local_model_path
from app.retrieval.index_manifest import (
    IndexGenerationCompatibilityError,
    IndexGenerationIntegrityError,
    assert_index_generation_mutable,
    assert_index_generation_ready,
    assert_packaged_index_generation_mapping,
    begin_index_generation,
    index_generation_manifest_path,
    mark_index_generation_building,
    prepare_index_generation_mutation,
    resume_index_generation,
    validate_packaged_index_generation,
    verify_index_generation_snapshot,
    write_ready_index_generation_manifest,
)
from app.retrieval.vector_search import (
    QdrantLocalIndex,
    VectorIndexConfigurationError,
    VectorSearchService,
)
from app.services.workflows import delete_article, index_pending_chunks


def _seed_indexed_chunks(database: Database, index: QdrantLocalIndex) -> tuple[int, int]:
    database.save_article_and_chunks(
        {
            "id": "article-manifest",
            "sha256": "m" * 64,
            "doi": None,
            "title": "Manifest provenance article",
            "authors": [],
            "pdf_path": "data/pdf/manifest.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "Measured acidity was 3.5 pH units.",
                "token_count": 8,
            },
            {
                "section": "Discussion",
                "page_start": 2,
                "page_end": 2,
                "chunk_index": 1,
                "text": "The result remained stable after ten days.",
                "token_count": 8,
            },
        ],
    )
    chunk_ids = tuple(int(row["id"]) for row in database.chunks_for_embedding(limit=10))
    index.upsert(
        EmbeddedChunkBatch(
            chunk_ids=chunk_ids,
            article_ids=("article-manifest", "article-manifest"),
            sections=("Results", "Discussion"),
            page_starts=(1, 2),
            page_ends=(1, 2),
            vectors=((1.0, 0.0), (0.0, 1.0)),
            model_name=index.model_name,
            vector_dimension=2,
        )
    )
    database.update_embedding_status(chunk_ids, "indexed")
    return chunk_ids


def _index(settings) -> QdrantLocalIndex:
    settings.qdrant.collection_name = "index-manifest-tests"
    settings.embeddings.model_name = "fake/index-manifest"
    model_path = local_model_path(settings)
    model_path.mkdir(parents=True, exist_ok=True)
    (model_path / "weights.bin").write_bytes(b"deterministic fake model weights")
    write_model_manifest(model_path, settings.embeddings.model_name)
    return QdrantLocalIndex(settings)


class _ManifestBackend:
    model_name = "fake/index-manifest"
    dimension = 2

    def __init__(self, *_: object, **__: object) -> None:
        pass

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.5, 0.5] for _ in texts]

    def encode_queries(self, texts: list[str]) -> list[list[float]]:
        return [[0.5, 0.5] for _ in texts]

    def close(self) -> None:
        return None


def test_manifest_records_and_verifies_exact_indexed_snapshot(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        _seed_indexed_chunks(database, index)

        manifest = write_ready_index_generation_manifest(database, index)

        assert manifest.state == "ready"
        assert manifest.indexed_chunk_count == 2
        assert manifest.qdrant_point_count == 2
        assert manifest.fully_indexed is True
        assert manifest.database_schema_version > 0
        assert index_generation_manifest_path(
            settings,
            qdrant_path=index.path,
            collection_name=index.collection_name,
        ).is_file()
        assert assert_index_generation_ready(index) == manifest
        assert verify_index_generation_snapshot(database, index) == manifest
    finally:
        index.close()


def test_recreate_generation_blocks_reads_before_any_vector_is_written(settings) -> None:
    index = _index(settings)
    try:
        manifest = begin_index_generation(index)

        assert manifest.state == "building"
        assert manifest.embedding_dimension == 0
        assert index_generation_manifest_path(
            settings,
            qdrant_path=index.path,
            collection_name=index.collection_name,
        ).is_file()
        with pytest.raises(IndexGenerationIntegrityError, match="not ready"):
            assert_index_generation_ready(index)
    finally:
        index.close()


def test_managed_generation_requires_a_verified_local_model(settings) -> None:
    settings.qdrant.collection_name = "unverified-model-generation"
    settings.embeddings.model_name = "fake/missing-manifest"
    index = QdrantLocalIndex(settings)
    try:
        with pytest.raises(IndexGenerationIntegrityError, match="verified local embedding model"):
            begin_index_generation(index)
    finally:
        index.close()


def test_managed_query_requires_the_verified_local_backend(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        _seed_indexed_chunks(database, index)
        write_ready_index_generation_manifest(database, index)
        with pytest.raises(VectorIndexConfigurationError, match="verified local embedding backend"):
            VectorSearchService(database, _ManifestBackend(), index).search("scientific question")
    finally:
        index.close()


def test_building_generation_resumes_only_with_its_original_contract(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        _seed_indexed_chunks(database, index)
        original = write_ready_index_generation_manifest(database, index)
        with pytest.raises(IndexGenerationIntegrityError, match="not marked building"):
            index.delete_points([1])

        building = prepare_index_generation_mutation(index)

        assert building is not None
        assert building.state == "building"
        assert resume_index_generation(index) == building
        assert assert_index_generation_mutable(index) == building
        with pytest.raises(IndexGenerationIntegrityError, match="not ready"):
            assert_index_generation_ready(index)

        completed = write_ready_index_generation_manifest(
            database,
            index,
            generation_id=building.generation_id,
            created_at=building.created_at,
        )
        assert completed.generation_id == original.generation_id
        assert completed.created_at == original.created_at

        mark_index_generation_building(index)
        settings.ingestion.target_tokens = 501
        with pytest.raises(IndexGenerationCompatibilityError, match="chunk_target_tokens"):
            resume_index_generation(index)
    finally:
        index.close()


def test_indexing_workflow_resumes_a_compatible_building_generation(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        _seed_indexed_chunks(database, index)
        write_ready_index_generation_manifest(database, index)
        database.save_article_and_chunks(
            {
                "id": "article-resume",
                "sha256": "r" * 64,
                "doi": None,
                "title": "Resumable article",
                "authors": [],
                "pdf_path": "data/pdf/resume.pdf",
                "validation_status": "validated",
                "source": "local",
            },
            [
                {
                    "section": "Results",
                    "page_start": 1,
                    "page_end": 1,
                    "chunk_index": 0,
                    "text": "A resumable indexed chunk.",
                    "token_count": 5,
                }
            ],
        )
        mark_index_generation_building(index)
    finally:
        index.close()

    monkeypatch.setattr("app.services.workflows.SentenceTransformerBackend", _ManifestBackend)
    report = index_pending_chunks(settings, database)

    reopened = QdrantLocalIndex(settings)
    try:
        assert report.chunks_indexed == 1
        assert assert_index_generation_ready(reopened) is not None
        assert verify_index_generation_snapshot(database, reopened).indexed_chunk_count == 3
    finally:
        reopened.close()


def test_pending_chunks_do_not_invalidate_ready_indexed_snapshot(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        _seed_indexed_chunks(database, index)
        manifest = write_ready_index_generation_manifest(database, index)
        database.save_article_and_chunks(
            {
                "id": "article-pending",
                "sha256": "p" * 64,
                "doi": None,
                "title": "Pending article",
                "authors": [],
                "pdf_path": "data/pdf/pending.pdf",
                "validation_status": "validated",
                "source": "local",
            },
            [
                {
                    "section": "Results",
                    "page_start": 1,
                    "page_end": 1,
                    "chunk_index": 0,
                    "text": "This chunk is pending embedding.",
                    "token_count": 6,
                }
            ],
        )

        assert assert_index_generation_ready(index) == manifest
        assert verify_index_generation_snapshot(database, index) == manifest
    finally:
        index.close()


def test_packaged_ready_manifest_requires_matching_staged_counts(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        _seed_indexed_chunks(database, index)
        manifest = write_ready_index_generation_manifest(database, index)
        path = index_generation_manifest_path(
            settings,
            qdrant_path=index.path,
            collection_name=index.collection_name,
        )

        assert (
            validate_packaged_index_generation(
                path,
                collection_name=index.collection_name,
                qdrant_point_count=2,
                indexed_chunk_count=2,
                collection_info=index.client.get_collection(index.collection_name),
            )
            == manifest
        )
        with pytest.raises(IndexGenerationIntegrityError, match="packaged Qdrant count"):
            validate_packaged_index_generation(
                path,
                collection_name=index.collection_name,
                qdrant_point_count=1,
                indexed_chunk_count=2,
                collection_info=index.client.get_collection(index.collection_name),
            )
    finally:
        index.close()


def test_packaged_mapping_rejects_same_count_with_a_wrong_qdrant_id(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        chunk_ids = _seed_indexed_chunks(database, index)
        manifest = write_ready_index_generation_manifest(database, index)
        with sqlite3.connect(database.path) as connection:
            assert_packaged_index_generation_mapping(
                connection,
                index.client,
                collection_name=index.collection_name,
                model_name=manifest.embedding_model_name,
            )

            index.client.delete(
                collection_name=index.collection_name,
                points_selector=models.PointIdsList(points=[chunk_ids[1]]),
                wait=True,
            )
            index.client.upsert(
                collection_name=index.collection_name,
                points=[
                    models.PointStruct(
                        id=999,
                        vector=[0.0, 1.0],
                        payload={
                            "kind": "chunk",
                            "chunk_id": 999,
                            "article_id": "article-manifest",
                            "section": "Discussion",
                            "page_start": 2,
                            "page_end": 2,
                            "model_name": index.model_name,
                        },
                    )
                ],
                wait=True,
            )
            with pytest.raises(IndexGenerationIntegrityError, match="point ids do not match"):
                assert_packaged_index_generation_mapping(
                    connection,
                    index.client,
                    collection_name=index.collection_name,
                    model_name=manifest.embedding_model_name,
                )
    finally:
        index.close()


def test_manifest_blocks_semantic_configuration_drift_and_building_state(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        _seed_indexed_chunks(database, index)
        write_ready_index_generation_manifest(database, index)
        settings.ingestion.target_tokens = 501

        with pytest.raises(IndexGenerationCompatibilityError, match="chunk_target_tokens"):
            assert_index_generation_ready(index)

        settings.ingestion.target_tokens = 500
        mark_index_generation_building(index)
        with pytest.raises(IndexGenerationIntegrityError, match="not ready"):
            assert_index_generation_ready(index)
    finally:
        index.close()


def test_manifest_detects_changed_indexed_text_and_qdrant_count(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        chunk_ids = _seed_indexed_chunks(database, index)
        write_ready_index_generation_manifest(database, index)
        with database.transaction() as connection:
            connection.execute(
                "UPDATE chunks SET text = ? WHERE id = ?",
                ("Changed scientific content.", chunk_ids[0]),
            )

        with pytest.raises(IndexGenerationIntegrityError, match="no longer matches"):
            verify_index_generation_snapshot(database, index)

        # Restore a fresh ready manifest, then make the SQLite/Qdrant counts disagree.
        write_ready_index_generation_manifest(database, index)
        index.client.delete(
            collection_name=index.collection_name,
            points_selector=models.PointIdsList(points=[chunk_ids[1]]),
            wait=True,
        )
        with pytest.raises(IndexGenerationIntegrityError, match="does not match"):
            verify_index_generation_snapshot(database, index)
    finally:
        index.close()


def test_explicit_verification_requires_exact_qdrant_chunk_ids(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        chunk_ids = _seed_indexed_chunks(database, index)
        write_ready_index_generation_manifest(database, index)
        index.client.delete(
            collection_name=index.collection_name,
            points_selector=models.PointIdsList(points=[chunk_ids[1]]),
            wait=True,
        )
        index.client.upsert(
            collection_name=index.collection_name,
            points=[
                models.PointStruct(
                    id=999,
                    vector=[0.0, 1.0],
                    payload={"kind": "chunk", "chunk_id": 999},
                )
            ],
            wait=True,
        )

        with pytest.raises(IndexGenerationIntegrityError, match="point ids do not match"):
            verify_index_generation_snapshot(database, index)
    finally:
        index.close()


def test_manifest_rejects_tampered_semantic_signature(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        _seed_indexed_chunks(database, index)
        write_ready_index_generation_manifest(database, index)
        path = index_generation_manifest_path(
            settings,
            qdrant_path=index.path,
            collection_name=index.collection_name,
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["semantic_signature_sha256"] = "0" * 64
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(IndexGenerationIntegrityError, match="semantic signature"):
            assert_index_generation_ready(index)
    finally:
        index.close()


def test_article_deletion_refreshes_an_existing_generation_manifest(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        _seed_indexed_chunks(database, index)
        write_ready_index_generation_manifest(database, index)
    finally:
        index.close()

    result = delete_article(settings, database, article_id="article-manifest")
    refreshed = QdrantLocalIndex(settings)
    try:
        assert result["deleted_chunks"] == 2
        assert result["deleted_vector_points"] == 2
        assert refreshed.count() == 0
        assert assert_index_generation_ready(refreshed) is not None
        assert verify_index_generation_snapshot(database, refreshed).indexed_chunk_count == 0
    finally:
        refreshed.close()
