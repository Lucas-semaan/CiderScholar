"""Read-only application overview and safe runtime configuration."""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import (
    get_common_corpus_database,
    get_database,
    get_private_corpus_database,
    get_settings,
)
from app.api.schemas import ConfirmedDesktopAction, RuntimeSettingsRequest
from app.config import Settings
from app.corpora import LocalProfile, load_local_profile
from app.corpus_packages.checks import refresh_corpus_update_if_due
from app.database.sqlite import Database
from app.deep_research.promotion import deep_research_availability
from app.desktop.app_updates import check_application_update
from app.desktop.supervisor import request_shutdown
from app.llm.argo_key import ArgoKeyStore
from app.memory_profiles import recommend_memory_profile
from app.publisher_access.credentials import PublisherCredentialStore
from app.services.workflows import apply_runtime_overrides, harvested_bibliographic_statistics

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/overview")
def overview(
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_database)],
    common_database: Annotated[Database, Depends(get_common_corpus_database)],
    private_database: Annotated[Database, Depends(get_private_corpus_database)],
) -> dict[str, Any]:
    queries = database.list_query_summaries(limit=1000)
    bibliography = harvested_bibliographic_statistics(database)
    return {
        "corpus": {
            "common": _corpus_statistics(common_database),
            "private": _corpus_statistics(private_database),
        },
        "bibliography": bibliography,
        "activity": {"queries": len(queries)},
        "runtime": runtime_payload(settings),
    }


def _corpus_statistics(database: Database) -> dict[str, int | float]:
    articles = database.list_articles(limit=5000)
    jobs = database.list_ingestion_jobs(limit=200)
    chunks = sum(int(row["chunk_count"] or 0) for row in articles)
    indexed_chunks = sum(int(row["indexed_chunk_count"] or 0) for row in articles)
    return {
        "articles": len(articles),
        "chunks": chunks,
        "indexed_chunks": indexed_chunks,
        "index_coverage": indexed_chunks / chunks if chunks else 0.0,
        "failed_ingestions": sum(job["state"] == "failed" for job in jobs),
        "ocr_required": sum(job["state"] == "ocr_required" for job in jobs),
    }


@router.get("/settings")
def runtime_settings(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    return runtime_payload(settings)


@router.put("/settings")
def update_runtime_settings(
    payload: RuntimeSettingsRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    updated = apply_runtime_overrides(
        settings,
        {
            "retrieval": {
                "default_article_count": payload.default_article_count,
                "lexical_weight": payload.lexical_weight,
                "vector_weight": payload.vector_weight,
                "reranker_weight": payload.reranker_weight,
            },
            "embeddings": {"batch_size": payload.embedding_batch_size},
            "evidence": {"passages_per_article": payload.passages_per_article},
        },
    )
    request.app.state.settings = updated
    return runtime_payload(updated)


@router.post("/shutdown")
def shutdown_application(
    payload: ConfirmedDesktopAction,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    """Ask the packaged supervisor to stop API and worker at their safe boundaries."""

    request_shutdown(settings.paths.data_dir / "runtime" / "shutdown.request")
    return {
        "state": "stopping",
        "message": "Arrêt demandé. Le travail actif est d'abord persisté puis terminé proprement.",
    }


def runtime_payload(settings: Settings) -> dict[str, Any]:
    memory = recommend_memory_profile(settings)
    corpus_update_check = refresh_corpus_update_if_due(settings)
    corpus_update = corpus_update_check.comparison
    application_update = check_application_update(settings)
    deep_research = deep_research_availability(settings)
    return {
        "offline_mode": settings.app.offline_mode,
        "bibliographic_apis": settings.app.allow_bibliographic_apis,
        "llm_provider": "argo",
        "llm_model": settings.argo.model,
        "llm_key_configured": (
            ArgoKeyStore(settings).configured() or bool(os.environ.get(settings.argo.api_key_env))
        ),
        "embedding_model": settings.embeddings.model_name,
        "embedding_device": settings.embeddings.device,
        "embedding_batch_size": settings.embeddings.batch_size,
        "passages_per_article": settings.evidence.passages_per_article,
        "database_name": settings.paths.database_path.name,
        "data_directory": str(settings.paths.data_dir),
        "administrator": load_local_profile() is LocalProfile.ADMIN,
        "memory": asdict(memory),
        "deep_research": deep_research.model_dump(mode="json"),
        "corpus_update": {
            **corpus_update.model_dump(mode="json"),
            "published_at": (
                corpus_update_check.published_at.isoformat()
                if corpus_update_check.published_at
                else None
            ),
        },
        "application_update": application_update.model_dump(mode="json"),
        "retrieval": {
            "lexical_weight": settings.retrieval.lexical_weight,
            "vector_weight": settings.retrieval.vector_weight,
            "reranker_weight": settings.retrieval.reranker_weight,
            "default_article_count": settings.retrieval.default_article_count,
        },
        "harvest": {
            "enabled": settings.harvest.enabled,
            "cadence_hours": settings.harvest.cadence_hours,
            "per_source_limit": settings.harvest.per_source_limit,
            "free_openalex_only": settings.harvest.openalex_free_only,
        },
        "publisher_access": {
            "enabled": (
                not settings.app.offline_mode
                and settings.app.allow_publisher_automation
                and settings.publisher_access.enabled
            ),
            "credentials_configured": PublisherCredentialStore(settings).configured(),
            "profiles": [
                {"id": profile.id, "label": profile.label}
                for profile in settings.publisher_access.profiles
            ],
            "max_records_per_run": settings.publisher_access.max_records_per_run,
        },
    }
