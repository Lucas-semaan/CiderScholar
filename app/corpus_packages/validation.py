"""Cross-store integrity checks required before publishing a common corpus."""

from __future__ import annotations

from qdrant_client import QdrantClient

from app.config import Settings
from app.corpus_packages.models import CorpusCounts
from app.corpus_packages.offline import CommonCorpusOfflineGuard


class CorpusCountValidationError(RuntimeError):
    """SQLite article/chunk state and Qdrant vector counts are inconsistent."""


def _vector_count(settings: Settings) -> int:
    qdrant_dir = settings.paths.common_qdrant_dir
    if not any(path.is_file() and path.name != ".lock" for path in qdrant_dir.rglob("*")):
        return 0
    client = QdrantClient(
        path=str(qdrant_dir),
        force_disable_check_same_thread=True,
        cloud_inference=False,
    )
    try:
        if not client.collection_exists(settings.qdrant.collection_name):
            return 0
        return int(client.count(settings.qdrant.collection_name, exact=True).count)
    finally:
        client.close()


def validate_corpus_counts(
    settings: Settings,
    guard: CommonCorpusOfflineGuard,
) -> CorpusCounts:
    """Require one persisted vector for every packaged chunk."""

    connection = guard.connection
    if connection is None:
        raise RuntimeError("offline corpus guard is not active")
    articles = int(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
    chunks = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    indexed = int(
        connection.execute(
            "SELECT COUNT(*) FROM chunks WHERE embedding_status = 'indexed'"
        ).fetchone()[0]
    )
    vectors = _vector_count(settings)
    if chunks != indexed or indexed != vectors:
        raise CorpusCountValidationError(
            "common corpus is inconsistent: "
            f"articles={articles}, chunks={chunks}, indexed={indexed}, vectors={vectors}"
        )
    return CorpusCounts(articles=articles, chunks=chunks, vectors=vectors)
