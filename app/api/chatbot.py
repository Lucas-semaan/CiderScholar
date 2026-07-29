"""Primary conversational interface over the local RAG and INRAE ARGO."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from app.api.dependencies import get_database, get_settings
from app.api.schemas import (
    ChatConversationCreateRequest,
    ChatConversationRenameRequest,
    ChatExportRequest,
    ChatFavoriteRequest,
    ChatFeedbackRequest,
    ChatJobSubmitRequest,
    ChatJobSubmitResponse,
    PersistedUserMessage,
)
from app.chat_exports import export_conversations
from app.config import Settings
from app.database.sqlite import Database
from app.deep_research.promotion import deep_research_availability
from app.jobs.contracts import ChatAnswerPayload, DeepResearchPayload
from app.jobs.repository import ActiveJobLimitError, JobRepository

router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])


def _with_active_jobs(database: Database, conversation: dict[str, Any]) -> dict[str, Any]:
    conversation_id = UUID(conversation["id"])
    active_jobs = [
        job.to_public().model_dump(mode="json")
        for job in JobRepository(database.path).list_active(conversation_id)
    ]
    return {
        **conversation,
        "active_job_count": len(active_jobs),
        "active_jobs": active_jobs,
    }


@router.get("/conversations")
def list_conversations(
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, Any]:
    return {"conversations": database.list_chat_conversations()}


@router.get("/conversations/search")
def search_conversations(
    database: Annotated[Database, Depends(get_database)],
    query: Annotated[str, Query(min_length=2, max_length=200)],
) -> dict[str, Any]:
    return {"conversations": database.search_chat_conversations(query)}


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ChatConversationCreateRequest,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, Any]:
    conversation = database.create_chat_conversation(payload.title)
    return _with_active_jobs(database, conversation)


@router.post(
    "/conversations/{conversation_id}/jobs",
    response_model=ChatJobSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_chat_job(
    conversation_id: UUID,
    payload: ChatJobSubmitRequest,
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatJobSubmitResponse:
    try:
        repository = JobRepository(database.path)
        if payload.mode == "deep_research":
            availability = deep_research_availability(settings)
            if not availability.available:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "deep_research_unavailable",
                        "state": availability.state,
                        "message": availability.message,
                    },
                )
            if payload.use_external_sources:
                raise HTTPException(
                    status_code=422,
                    detail="L’analyse approfondie utilise uniquement les corpus locaux qualifiés.",
                )
            enqueued = repository.enqueue_deep_research(
                DeepResearchPayload(
                    message=payload.message,
                    conversation_id=conversation_id,
                    client_request_id=payload.client_request_id,
                )
            )
        else:
            enqueued = repository.enqueue_chat(
                ChatAnswerPayload(
                    message=payload.message,
                    conversation_id=conversation_id,
                    client_request_id=payload.client_request_id,
                    use_external_sources=payload.use_external_sources,
                    interaction_mode=payload.interaction_mode,
                )
            )
    except ActiveJobLimitError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "active_job_limit",
                "message": "Attendez la fin d'un travail actif avant d'en envoyer un autre.",
                "limit": error.limit,
            },
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Conversation introuvable.") from error
    return ChatJobSubmitResponse(
        job=enqueued.job.to_public(),
        user_message=PersistedUserMessage(
            id=enqueued.user_message_id,
            content=enqueued.user_message_content,
            created_at=enqueued.user_message_created_at,
        ),
    )


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, Any]:
    conversation = database.chat_conversation(str(conversation_id))
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")
    return _with_active_jobs(database, conversation)


@router.put("/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: UUID,
    payload: ChatConversationRenameRequest,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, Any]:
    conversation = database.rename_chat_conversation(str(conversation_id), payload.title)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")
    return _with_active_jobs(database, conversation)


@router.put("/conversations/{conversation_id}/favorite")
def favorite_conversation(
    conversation_id: UUID,
    payload: ChatFavoriteRequest,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, bool]:
    if not database.set_chat_conversation_favorite(str(conversation_id), payload.favorite):
        raise HTTPException(status_code=404, detail="Conversation introuvable.")
    return {"favorite": payload.favorite}


@router.put("/messages/{message_id}/feedback")
def save_message_feedback(
    message_id: UUID,
    payload: ChatFeedbackRequest,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, bool]:
    try:
        saved = database.set_chat_message_feedback(str(message_id), payload.helpful)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not saved:
        raise HTTPException(status_code=404, detail="Message introuvable.")
    return {"helpful": payload.helpful}


@router.post("/exports", response_class=FileResponse)
def export_chat_conversations(
    payload: ChatExportRequest,
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    try:
        path = export_conversations(
            database,
            conversation_ids=[str(item) for item in payload.conversation_ids],
            message_ids=[str(item) for item in payload.message_ids],
            format=payload.format,
            destination_root=settings.paths.exports_dir / "conversations",
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    media_type = "text/markdown" if payload.format == "markdown" else "application/pdf"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, bool]:
    if not database.delete_chat_conversation(str(conversation_id)):
        raise HTTPException(status_code=404, detail="Conversation introuvable.")
    return {"deleted": True}
