"""Corpus ingestion and local index administration routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.dependencies import get_common_corpus_database, get_settings
from app.api.schemas import FolderIngestionRequest, IndexRequest
from app.api.serialization import corpus_listing, serialize_row
from app.config import Settings
from app.corpora import (
    CorpusMutationForbiddenError,
    CorpusScope,
    authorize_corpus_mutation,
    load_local_profile,
)
from app.database.sqlite import Database
from app.services.workflows import (
    delete_article,
    index_pending_chunks,
    ingest_paths,
    pdf_paths,
    reindex_article,
    save_uploaded_pdf,
)

router = APIRouter(prefix="/api/corpus", tags=["corpus"])


def _authorize_common_mutation() -> None:
    try:
        authorize_corpus_mutation(CorpusScope.COMMON, load_local_profile())
    except CorpusMutationForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("")
def corpus(
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    return corpus_listing(database, scope=CorpusScope.COMMON)


@router.get("/{article_id}/chunks")
def article_chunks(
    article_id: str,
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    chunks = [serialize_row(row) for row in database.chunks_for_article(article_id, limit=500)]
    return {"article_id": article_id, "chunks": chunks}


@router.post("/upload")
def upload_pdfs(
    files: Annotated[list[UploadFile], File(description="PDF scientifiques")],
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    _authorize_common_mutation()
    if not files:
        raise ValueError("at least one PDF is required")
    paths = []
    for uploaded in files:
        if not uploaded.filename:
            raise ValueError("uploaded PDF has no file name")
        paths.append(
            save_uploaded_pdf(
                settings,
                original_name=uploaded.filename,
                stream=uploaded.file,
            )
        )
    reports = ingest_paths(settings, database, paths)
    return {"reports": [report.model_dump(mode="json") for report in reports]}


@router.post("/folder")
def ingest_folder(
    payload: FolderIngestionRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    _authorize_common_mutation()
    paths = list(pdf_paths(payload.folder, recursive=payload.recursive))
    reports = ingest_paths(settings, database, paths) if paths else []
    return {
        "discovered_files": len(paths),
        "reports": [report.model_dump(mode="json") for report in reports],
    }


@router.post("/index")
def index_corpus(
    payload: IndexRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    _authorize_common_mutation()
    report = index_pending_chunks(
        settings,
        database,
        retry_failed=payload.retry_failed,
    )
    return report.model_dump(mode="json")


@router.post("/{article_id}/reindex")
def reindex_corpus_article(
    article_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    _authorize_common_mutation()
    return reindex_article(settings, database, article_id=article_id).model_dump(mode="json")


@router.delete("/{article_id}")
def delete_corpus_article(
    article_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, int]:
    _authorize_common_mutation()
    return delete_article(settings, database, article_id=article_id)
