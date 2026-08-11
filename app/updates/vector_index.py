"""Local vector indexing and hybrid retrieval for harvested abstracts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from qdrant_client import models

from app.config import Settings
from app.ingestion.embeddings import EmbeddingBackend
from app.retrieval.lexical_search import LexicalQueryBuilder
from app.retrieval.vector_search import QdrantLocalIndex
from app.updates.harvest import BibliographicHarvestStore, infer_cider_themes

CIDER_QUERY_EXPANSIONS: dict[str, str] = {
    "biochimie": "biochemistry metabolism organic acids sugar ethanol glycerol kinetics",
    "microbiologie": (
        "microbiology microorganisms yeast bacteria microbial ecology malolactic Oenococcus "
        "Saccharomyces non-Saccharomyces Hanseniaspora lactic acid bacteria acetic acid bacteria "
        "spoilage Brettanomyces Zygosaccharomyces Alicyclobacillus Penicillium patulin "
        "foodborne pathogens Escherichia coli Salmonella Listeria"
    ),
    "polyphenols": "polyphenols phenolic tannins procyanidins oxidation colour astringency",
    "proteines": ("protein peptides nitrogen amino acids yeast assimilable nitrogen YAN nutrition"),
    "jus_pomme": "apple juice must pressing clarification pectin filtration composition",
    "calvados_eau_vie": (
        "calvados apple brandy apple spirit cider brandy distillation barrel maturation "
        "oak ageing wood aging volatiles esters phenolics tannins acidity oxidation color "
        "colour mouthfeel sensory"
    ),
    "pommeau": "pommeau apple mistelle mutage fortified composition turbidity",
    "aromes_procede": "aroma volatile sensory flavour processing fermentation quality",
}


class BibliographicIndexReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str
    records_indexed: int = Field(ge=0)
    records_failed: int = Field(ge=0)
    records_pruned: int = Field(default=0, ge=0)
    eligible_records: int = Field(default=0, ge=0)
    records_marked_not_applicable: int = Field(default=0, ge=0)
    records_requeued: int = Field(default=0, ge=0)
    batches_completed: int = Field(ge=0)
    duration_seconds: float = Field(ge=0.0)
    error_type: str | None = None
    error_message: str | None = None


class BibliographicIndexVerification(BaseModel):
    """Exact, model-free consistency check for the abstract-only index."""

    model_config = ConfigDict(extra="forbid")

    collection_name: str
    eligible_record_count: int = Field(ge=0)
    indexed_record_count: int = Field(ge=0)
    qdrant_point_count: int = Field(ge=0)
    verified: bool = True


class BibliographicHybridResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    record_id: str
    title: str
    abstract: str
    authors: list[str]
    journal: str | None
    publication_year: int | None
    doi: str | None
    url: str | None
    sources: list[str]
    lexical_rank: int | None = Field(default=None, ge=1)
    vector_rank: int | None = Field(default=None, ge=1)
    score: float = Field(ge=0.0)


class BibliographicHybridResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    results: list[BibliographicHybridResult]
    lexical_candidate_count: int = Field(default=0, ge=0)
    dense_candidate_count: int = Field(default=0, ge=0)
    rrf_unique_candidate_count: int = Field(default=0, ge=0)
    duration_seconds: float = Field(ge=0.0)


class BibliographicVectorIndex:
    """Separate Qdrant collection so abstract records never impersonate PDF chunks."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.index = QdrantLocalIndex(
            settings,
            collection_name=settings.harvest.vector_collection_name,
        )

    @property
    def collection_name(self) -> str:
        return self.settings.harvest.vector_collection_name

    def upsert(
        self,
        *,
        record_ids: Sequence[str],
        vectors: Any,
        vector_dimension: int,
    ) -> None:
        if not record_ids:
            raise ValueError("bibliographic vector batch cannot be empty")
        rows, columns = _matrix_shape(vectors)
        if rows != len(record_ids) or columns != vector_dimension:
            raise ValueError("bibliographic embedding matrix has an invalid shape")
        self.index.ensure_collection(vector_dimension)
        self.index.client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=record_id,
                    vector=_float_vector(vectors[position]),
                    payload={
                        "kind": "bibliographic_abstract",
                        "record_id": record_id,
                        "model_name": self.settings.embeddings.model_name,
                    },
                )
                for position, record_id in enumerate(record_ids)
            ],
            wait=True,
        )

    def search(self, query_vector: Any, *, limit: int = 50) -> list[tuple[str, float]]:
        if not 1 <= limit <= 500:
            raise ValueError("bibliographic vector limit must be between 1 and 500")
        vector = _float_vector(query_vector)
        if not self.index.collection_exists():
            return []
        self.index.ensure_collection(len(vector))
        response = self.index.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="kind",
                        match=models.MatchValue(value="bibliographic_abstract"),
                    )
                ]
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        references: list[tuple[str, float]] = []
        for point in response.points:
            payload = point.payload or {}
            record_id = payload.get("record_id")
            if not isinstance(record_id, str) or str(point.id) != record_id:
                raise RuntimeError("invalid bibliographic Qdrant payload")
            references.append((record_id, float(point.score)))
        return references

    def count(self) -> int:
        if not self.index.collection_exists():
            return 0
        return int(
            self.index.client.count(
                collection_name=self.collection_name,
                count_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="kind",
                            match=models.MatchValue(value="bibliographic_abstract"),
                        )
                    ]
                ),
                exact=True,
            ).count
        )

    def record_ids(self) -> list[str]:
        if not self.index.collection_exists():
            return []
        record_ids: list[str] = []
        offset: int | str | None = None
        while True:
            points, offset = self.index.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="kind",
                            match=models.MatchValue(value="bibliographic_abstract"),
                        )
                    ]
                ),
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                record_id = payload.get("record_id")
                if not isinstance(record_id, str) or str(point.id) != record_id:
                    raise RuntimeError("invalid bibliographic Qdrant payload")
                record_ids.append(record_id)
            if offset is None:
                break
        return record_ids

    def record_payloads(self) -> dict[str, dict[str, object]]:
        """Read only routing payloads, never abstracts or vectors, for verification."""

        if not self.index.collection_exists():
            return {}
        payloads: dict[str, dict[str, object]] = {}
        offset: int | str | None = None
        while True:
            points, offset = self.index.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="kind",
                            match=models.MatchValue(value="bibliographic_abstract"),
                        )
                    ]
                ),
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                record_id = payload.get("record_id")
                if (
                    not isinstance(record_id, str)
                    or str(point.id) != record_id
                    or record_id in payloads
                ):
                    raise RuntimeError("invalid bibliographic Qdrant payload")
                payloads[record_id] = dict(payload)
            if offset is None:
                return payloads

    def delete(self, record_ids: Sequence[str]) -> int:
        if not record_ids or not self.index.collection_exists():
            return 0
        before = self.count()
        self.index.client.delete(
            collection_name=self.collection_name,
            points_selector=models.PointIdsList(points=list(record_ids)),
            wait=True,
        )
        return max(0, before - self.count())

    def delete_collection(self) -> bool:
        return self.index.delete_collection()

    def close(self) -> None:
        self.index.close()

    def __enter__(self) -> BibliographicVectorIndex:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def index_bibliographic_abstracts(
    settings: Settings,
    store: BibliographicHarvestStore,
    backend: EmbeddingBackend,
    *,
    close_backend: bool = True,
    recreate: bool = False,
    retry_failed: bool = True,
    raise_on_error: bool = True,
) -> BibliographicIndexReport:
    started = perf_counter()
    indexed = 0
    failed = 0
    pruned = 0
    batches = 0
    marked_not_applicable = 0
    requeued = 0
    error_type: str | None = None
    error_message: str | None = None
    index = BibliographicVectorIndex(settings)
    try:
        # Hold the common Qdrant resource lock before changing any SQLite
        # lifecycle state, so a reader or another writer cannot observe a
        # mixed abstract generation.
        _ = index.index.client
        marked_not_applicable, requeued = store.synchronize_abstract_index_eligibility()
        if recreate:
            index.delete_collection()
            store.reset_abstract_embedding_statuses()
        eligible_ids = set(store.eligible_record_ids())
        prunable_ids = set(index.record_ids()) - eligible_ids
        pruned = index.delete(sorted(prunable_ids))
        batch_size = settings.embeddings.batch_size
        while rows := store.pending_abstracts(limit=5000, retry_failed=retry_failed):
            batch = rows[:batch_size]
            record_ids = [str(row["id"]) for row in batch]
            texts = [f"{row['title']}\n{str(row['abstract'])[:12000]}" for row in batch]
            try:
                vectors = backend.encode_documents(texts)
                index.upsert(
                    record_ids=record_ids,
                    vectors=vectors,
                    vector_dimension=backend.dimension,
                )
                store.update_embedding_status(record_ids, "indexed")
                indexed += len(record_ids)
                batches += 1
            except Exception as exc:
                store.update_embedding_status(record_ids, "failed")
                failed += len(record_ids)
                error_type = type(exc).__name__
                error_message = str(exc)[:1000]
                if raise_on_error:
                    raise
                break
    finally:
        index.close()
        if close_backend:
            backend.close()
    return BibliographicIndexReport(
        model_name=backend.model_name,
        records_indexed=indexed,
        records_failed=failed,
        records_pruned=pruned,
        eligible_records=len(store.eligible_record_ids()),
        records_marked_not_applicable=marked_not_applicable,
        records_requeued=requeued,
        batches_completed=batches,
        duration_seconds=perf_counter() - started,
        error_type=error_type,
        error_message=error_message,
    )


def verify_bibliographic_abstract_index(
    settings: Settings,
    store: BibliographicHarvestStore,
) -> BibliographicIndexVerification:
    """Verify IDs, routing payloads and SQLite states without loading E5."""

    index = BibliographicVectorIndex(settings)
    try:
        _ = index.index.client
        expected_ids = set(store.eligible_record_ids())
        payloads = index.record_payloads()
        actual_ids = set(payloads)
        total_points = (
            int(index.index.client.count(collection_name=index.collection_name, exact=True).count)
            if index.index.collection_exists()
            else 0
        )
        if total_points != len(actual_ids):
            raise RuntimeError("bibliographic collection contains a non-abstract point")
        if actual_ids != expected_ids:
            raise RuntimeError(
                "bibliographic Qdrant point ids do not match eligible SQLite records"
            )
        for record_id, payload in payloads.items():
            if (
                payload.get("kind") != "bibliographic_abstract"
                or payload.get("record_id") != record_id
                or payload.get("model_name") != settings.embeddings.model_name
            ):
                raise RuntimeError(
                    "bibliographic Qdrant payload does not match SQLite routing data"
                )
        non_indexed = {
            record_id: status
            for record_id, status in store.eligible_abstract_embedding_statuses().items()
            if status != "indexed"
        }
        if non_indexed:
            raise RuntimeError("eligible bibliographic records are not all marked indexed")
        return BibliographicIndexVerification(
            collection_name=index.collection_name,
            eligible_record_count=len(expected_ids),
            indexed_record_count=len(actual_ids),
            qdrant_point_count=total_points,
        )
    finally:
        index.close()


class BibliographicHybridSearchService:
    """Fuse local FTS5 and E5 results while preserving abstract provenance."""

    def __init__(
        self,
        settings: Settings,
        store: BibliographicHarvestStore,
        backend: EmbeddingBackend,
        index: BibliographicVectorIndex,
    ) -> None:
        self.settings = settings
        self.store = store
        self.backend = backend
        self.index = index
        self.query_builder = LexicalQueryBuilder(settings)

    def search(self, query: str, *, limit: int = 20) -> BibliographicHybridResponse:
        started = perf_counter()
        if not query.strip():
            raise ValueError("bibliographic hybrid query cannot be empty")
        if not 1 <= limit <= 100:
            raise ValueError("bibliographic hybrid limit must be between 1 and 100")
        # BibliographicHarvestStore.search is deliberately capped at 200.
        # Keep wider UI result requests valid instead of constructing an
        # internal limit that the authoritative store rejects.
        candidate_limit = min(max(limit * 5, 50), 200)
        expanded_query = expand_cider_query(query)
        prepared = self.query_builder.build(expanded_query)
        full_text_rows = self.store.search(prepared.fts5_expression, limit=candidate_limit)
        metadata_rows = self.store.search_metadata(query, limit=candidate_limit)
        lexical_rows = list(
            {str(row["id"]): row for row in [*full_text_rows, *metadata_rows]}.values()
        )
        query_vectors = self.backend.encode_queries([expanded_query])
        vector_rows = self.index.search(query_vectors[0], limit=candidate_limit)
        lexical_ranks = {str(row["id"]): rank for rank, row in enumerate(lexical_rows, start=1)}
        vector_ranks = {
            record_id: rank for rank, (record_id, _score) in enumerate(vector_rows, start=1)
        }
        scores: dict[str, float] = {}
        rrf_k = self.settings.retrieval.rrf_k
        for record_id, rank in lexical_ranks.items():
            scores[record_id] = scores.get(record_id, 0.0) + 0.4 / (rrf_k + rank)
        for record_id, rank in vector_ranks.items():
            scores[record_id] = scores.get(record_id, 0.0) + 0.6 / (rrf_k + rank)
        candidate_records = self.store.records_by_ids(list(scores))
        query_themes = infer_cider_themes(query)
        if query_themes:
            for record_id, row in candidate_records.items():
                document_themes = infer_cider_themes(f"{row['title']}\n{row['abstract'] or ''}")
                if query_themes & document_themes:
                    scores[record_id] += 0.02
        ordered_ids = sorted(
            scores,
            key=lambda record_id: (
                -scores[record_id],
                lexical_ranks.get(record_id, 10**9),
                vector_ranks.get(record_id, 10**9),
                record_id,
            ),
        )[:limit]
        records = {
            record_id: candidate_records[record_id]
            for record_id in ordered_ids
            if record_id in candidate_records
        }
        results: list[BibliographicHybridResult] = []
        for rank, record_id in enumerate(ordered_ids, start=1):
            row = records.get(record_id)
            if row is None or not isinstance(row["abstract"], str):
                continue
            try:
                authors = json.loads(row["authors"] or "[]")
            except json.JSONDecodeError:
                authors = []
            results.append(
                BibliographicHybridResult(
                    rank=rank,
                    record_id=record_id,
                    title=str(row["title"]),
                    abstract=str(row["abstract"]),
                    authors=[str(author) for author in authors],
                    journal=(str(row["journal"]) if row["journal"] else None),
                    publication_year=(
                        int(row["publication_year"])
                        if row["publication_year"] is not None
                        else None
                    ),
                    doi=str(row["doi"]) if row["doi"] else None,
                    url=str(row["url"]) if row["url"] else None,
                    sources=(str(row["sources"]).split(",") if row["sources"] else []),
                    lexical_rank=lexical_ranks.get(record_id),
                    vector_rank=vector_ranks.get(record_id),
                    score=scores[record_id],
                )
            )
        return BibliographicHybridResponse(
            query=query.strip(),
            results=results,
            lexical_candidate_count=len(lexical_rows),
            dense_candidate_count=len(vector_rows),
            rrf_unique_candidate_count=len(scores),
            duration_seconds=perf_counter() - started,
        )

    def close(self) -> None:
        self.backend.close()
        self.index.close()


def expand_cider_query(query: str) -> str:
    """Add deterministic bilingual cider vocabulary without calling a model."""

    cleaned = " ".join(query.split())
    themes = infer_cider_themes(cleaned)
    expansions = [CIDER_QUERY_EXPANSIONS[theme] for theme in sorted(themes)]
    return " ".join([cleaned, *expansions])


def _matrix_shape(vectors: Any) -> tuple[int, int]:
    shape = getattr(vectors, "shape", None)
    if shape is not None and len(shape) == 2:
        return int(shape[0]), int(shape[1])
    rows = len(vectors)
    columns = len(vectors[0]) if rows else 0
    return rows, columns


def _float_vector(vector: Any) -> list[float]:
    vector = vector.tolist() if hasattr(vector, "tolist") else list(vector)
    if not isinstance(vector, list) or any(isinstance(value, (list, tuple)) for value in vector):
        raise ValueError("expected one flat bibliographic vector")
    return [float(value) for value in vector]
