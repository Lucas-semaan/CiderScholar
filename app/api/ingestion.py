"""Corpus ingestion and local index administration routes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse

from app.api.dependencies import get_common_corpus_database, get_common_corpus_settings
from app.api.schemas import FolderIngestionRequest, IndexRequest
from app.api.serialization import corpus_listing, serialize_row
from app.config import Settings
from app.database.sqlite import Database
from app.services.workflows import (
    delete_article,
    index_pending_chunks,
    ingest_and_index_paths,
    pdf_paths,
    reindex_article,
    save_uploaded_pdf,
)

router = APIRouter(prefix="/api/corpus", tags=["corpus"])


@router.get("")
def corpus(
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    return corpus_listing(database)


@router.get("/{article_id}/chunks")
def article_chunks(
    article_id: str,
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    chunks = [serialize_row(row) for row in database.chunks_for_article(article_id, limit=500)]
    return {"article_id": article_id, "chunks": chunks}


@router.get("/{article_id}/pdf", response_class=FileResponse)
def article_pdf(
    article_id: str,
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> FileResponse:
    """Open the persisted source PDF selected by an explicit corpus article id."""

    article = database.article_details_by_ids([article_id]).get(article_id)
    if article is None:
        raise FileNotFoundError("Document PDF introuvable dans la base documentaire.")
    path = Path(str(article["pdf_path"])).resolve()
    if path.suffix.casefold() != ".pdf" or not path.is_file():
        raise FileNotFoundError("Le fichier PDF de ce document n’est plus disponible.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="inline",
    )


@router.post("/upload")
def upload_pdfs(
    files: Annotated[list[UploadFile], File(description="PDF scientifiques")],
    settings: Annotated[Settings, Depends(get_common_corpus_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
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
    reports, _indexing = ingest_and_index_paths(settings, database, paths)
    return {"reports": [report.model_dump(mode="json") for report in reports]}


@router.post("/folder")
def ingest_folder(
    payload: FolderIngestionRequest,
    settings: Annotated[Settings, Depends(get_common_corpus_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    paths = list(pdf_paths(payload.folder, recursive=payload.recursive))
    reports, _indexing = ingest_and_index_paths(settings, database, paths) if paths else ([], None)
    return {
        "discovered_files": len(paths),
        "reports": [report.model_dump(mode="json") for report in reports],
    }


@router.post("/index")
def index_corpus(
    payload: IndexRequest,
    settings: Annotated[Settings, Depends(get_common_corpus_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    report = index_pending_chunks(
        settings,
        database,
        retry_failed=payload.retry_failed,
    )
    return report.model_dump(mode="json")


@router.post("/{article_id}/reindex")
def reindex_corpus_article(
    article_id: str,
    settings: Annotated[Settings, Depends(get_common_corpus_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    return reindex_article(settings, database, article_id=article_id).model_dump(mode="json")


@router.delete("/{article_id}")
def delete_corpus_article(
    article_id: str,
    settings: Annotated[Settings, Depends(get_common_corpus_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, int]:
    return delete_article(settings, database, article_id=article_id)
