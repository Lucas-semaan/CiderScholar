"""Private document routes backed only by private SQLite, PDF and Qdrant paths."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, UploadFile, status

from app.api.dependencies import get_database, get_private_corpus_database, get_settings
from app.api.schemas import FolderIngestionRequest, IndexRequest
from app.api.serialization import corpus_listing
from app.config import Settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.jobs.contracts import JobPublic, PrivateIngestionPayload
from app.jobs.repository import PRIVATE_INGESTION_CONVERSATION_ID, JobRepository
from app.services.workflows import (
    delete_article,
    index_pending_chunks,
    pdf_paths,
    reindex_article,
    save_uploaded_pdf,
)

router = APIRouter(prefix="/api/private-corpus", tags=["private-corpus"])


def _stage_pdf(private_settings: Settings, source: Path) -> Path:
    with source.open("rb") as stream:
        return save_uploaded_pdf(
            private_settings,
            original_name=source.name,
            stream=stream,
        )


def _enqueue_private_ingestion(database: Database, staged: list[Path]) -> JobPublic:
    if not staged:
        raise ValueError("at least one private PDF is required")
    if len(staged) > 100:
        raise ValueError("at most 100 private PDFs can be queued together")
    job = JobRepository(database.path).enqueue_private_ingestion(
        PrivateIngestionPayload(
            staged_files=[f"uploads/{path.name}" for path in staged],
            conversation_id=PRIVATE_INGESTION_CONVERSATION_ID,
            client_request_id=uuid4(),
        )
    )
    return job.to_public()


def _summary(database: Database) -> dict[str, Any]:
    return corpus_listing(database, scope=CorpusScope.PRIVATE)


@router.get("")
def private_corpus(
    database: Annotated[Database, Depends(get_private_corpus_database)],
) -> dict[str, Any]:
    return _summary(database)


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
def upload_private_pdfs(
    files: Annotated[list[UploadFile], File(description="PDF privés")],
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, Any]:
    if not files:
        raise ValueError("at least one PDF is required")
    if len(files) > 100:
        raise ValueError("at most 100 private PDFs can be queued together")
    private_settings = settings_for_corpus(settings, CorpusScope.PRIVATE)
    paths = []
    for uploaded in files:
        if not uploaded.filename:
            raise ValueError("uploaded PDF has no file name")
        paths.append(
            save_uploaded_pdf(
                private_settings,
                original_name=uploaded.filename,
                stream=uploaded.file,
            )
        )
    return {
        "staged_files": len(paths),
        "job": _enqueue_private_ingestion(database, paths).model_dump(mode="json"),
    }


@router.post("/folder", status_code=status.HTTP_202_ACCEPTED)
def ingest_private_folder(
    payload: FolderIngestionRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, Any]:
    private_settings = settings_for_corpus(settings, CorpusScope.PRIVATE)
    paths = list(pdf_paths(payload.folder, recursive=payload.recursive))
    if not paths:
        raise ValueError("at least one private PDF is required")
    if len(paths) > 100:
        raise ValueError("at most 100 private PDFs can be queued together")
    staged = [_stage_pdf(private_settings, path) for path in paths]
    return {
        "discovered_files": len(paths),
        "job": _enqueue_private_ingestion(database, staged).model_dump(mode="json"),
    }


@router.post("/index")
def index_private_corpus(
    payload: IndexRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_private_corpus_database)],
) -> dict[str, Any]:
    report = index_pending_chunks(
        settings_for_corpus(settings, CorpusScope.PRIVATE),
        database,
        retry_failed=payload.retry_failed,
    )
    return report.model_dump(mode="json")


@router.post("/{article_id}/reindex")
def reindex_private_article(
    article_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_private_corpus_database)],
) -> dict[str, Any]:
    report = reindex_article(
        settings_for_corpus(settings, CorpusScope.PRIVATE),
        database,
        article_id=article_id,
    )
    return report.model_dump(mode="json")


@router.delete("/{article_id}")
def delete_private_article(
    article_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_private_corpus_database)],
) -> dict[str, int]:
    return delete_article(
        settings_for_corpus(settings, CorpusScope.PRIVATE),
        database,
        article_id=article_id,
    )
