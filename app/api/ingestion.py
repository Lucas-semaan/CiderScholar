"""Corpus ingestion and local index administration routes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi import Path as ApiPath
from fastapi.responses import FileResponse

from app.api.dependencies import get_common_corpus_database, get_common_corpus_settings
from app.api.schemas import (
    ChunkCorrectionDecisionRequest,
    ChunkCorrectionRequest,
    FolderIngestionRequest,
    IndexRequest,
)
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


@router.get("/{article_id}/native-source")
def native_source_view(
    article_id: str,
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    """Return safe, structured native-source passages for the local viewer."""

    source = database.native_source_view(article_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document introuvable dans la base documentaire.",
        )
    return {"article_id": article_id, **source}


@router.get("/{article_id}/tables")
def article_tables(
    article_id: str,
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    """Expose deterministic source-table cells for local inspection only."""

    if database.article_details_by_ids([article_id]).get(article_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document introuvable.")
    return {
        "article_id": article_id,
        "tables": [table.model_dump(mode="json") for table in database.table_evidence(article_id)],
    }


@router.get("/{article_id}/figures")
def article_figures(
    article_id: str,
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    """Expose source figure captions for local inspection, never generated analysis."""

    if database.article_details_by_ids([article_id]).get(article_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document introuvable.")
    return {
        "article_id": article_id,
        "figures": [
            figure.model_dump(mode="json") for figure in database.figure_evidence(article_id)
        ],
    }


@router.get("/{article_id}/inspection")
def article_inspection(
    article_id: str,
    database: Annotated[Database, Depends(get_common_corpus_database)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Inspect bounded provenance and structure without returning chunk source text."""

    inspection = database.article_inspection(article_id, limit=limit, offset=offset)
    if inspection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document introuvable dans la base documentaire.",
        )
    return inspection


@router.get("/{article_id}/corrections")
def article_corrections(
    article_id: str, database: Annotated[Database, Depends(get_common_corpus_database)]
) -> dict[str, Any]:
    if database.article_inspection(article_id, limit=1, offset=0) is None:
        raise HTTPException(
            status_code=404, detail="Document introuvable dans la base documentaire."
        )
    return {"article_id": article_id, "corrections": database.chunk_corrections(article_id)}


@router.post("/{article_id}/corrections", status_code=status.HTTP_201_CREATED)
def propose_article_correction(
    article_id: str,
    payload: ChunkCorrectionRequest,
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, str]:
    if payload.chunk_id not in database.article_chunk_ids(article_id):
        raise HTTPException(status_code=404, detail="Fragment introuvable pour ce document.")
    correction_id = database.propose_chunk_correction(**payload.model_dump())
    return {"id": correction_id, "state": "proposed"}


@router.post("/{article_id}/corrections/{correction_id}/decision")
def decide_article_correction(
    article_id: str,
    correction_id: str,
    payload: ChunkCorrectionDecisionRequest,
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, str]:
    if correction_id not in {item["id"] for item in database.chunk_corrections(article_id)}:
        raise HTTPException(status_code=404, detail="Correction introuvable pour ce document.")
    database.decide_chunk_correction(correction_id, approved=payload.decision == "approved")
    return {"id": correction_id, "state": payload.decision}


@router.get("/{article_id:path}/pdf", response_class=FileResponse)
def article_pdf(
    article_id: Annotated[str, ApiPath(min_length=1, max_length=100)],
    settings: Annotated[Settings, Depends(get_common_corpus_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> FileResponse:
    """Open the persisted source PDF selected by an explicit corpus article id."""

    article = database.article_details_by_ids([article_id]).get(article_id)
    if article is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document PDF introuvable dans la base documentaire.",
        )
    # The client selects only an article id.  The persisted path can belong to a
    # legacy corpus until its explicit, additive migration is completed.
    path = Path(str(article["pdf_path"])).resolve()
    if path.suffix.casefold() != ".pdf" or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Le fichier PDF de ce document n’est plus disponible.",
        )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename="article.pdf",
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
