"""Explicit endpoints for authorized publisher authentication and collection."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.api.dependencies import get_database, get_settings
from app.api.schemas import PublisherCollectionRequest, PublisherCredentialRequest
from app.config import Settings
from app.database.sqlite import Database
from app.publisher_access.credentials import PublisherCredentialStore
from app.publisher_access.service import PublisherCollectionService, profile_by_id

router = APIRouter(prefix="/api/publisher-access", tags=["publisher-access"])


def _require_enabled(settings: Settings) -> None:
    if (
        settings.app.offline_mode
        or not settings.app.allow_publisher_automation
        or not settings.publisher_access.enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="L’automatisation éditeur n’est pas activée dans la configuration locale.",
        )


@router.get("/status")
def publisher_access_status(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    store = PublisherCredentialStore(settings)
    return {
        "enabled": (
            not settings.app.offline_mode
            and settings.app.allow_publisher_automation
            and settings.publisher_access.enabled
        ),
        "credentials_configured": store.configured(),
        "profiles": [
            {"id": profile.id, "label": profile.label}
            for profile in settings.publisher_access.profiles
        ],
        "max_records_per_run": settings.publisher_access.max_records_per_run,
    }


@router.put("/credentials")
def save_publisher_credentials(
    payload: PublisherCredentialRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, bool]:
    _require_enabled(settings)
    PublisherCredentialStore(settings).save(
        username=payload.username,
        password=payload.password,
    )
    return {"credentials_configured": True}


@router.delete("/credentials")
def delete_publisher_credentials(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, bool]:
    _require_enabled(settings)
    PublisherCredentialStore(settings).delete()
    return {"credentials_configured": False}


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def start_publisher_collection(
    payload: PublisherCollectionRequest,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, Any]:
    _require_enabled(settings)
    profile = profile_by_id(settings, payload.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profil éditeur inconnu.")
    if not PublisherCredentialStore(settings).configured():
        raise HTTPException(status_code=409, detail="Identifiants LDAP non configurés.")
    if len(payload.targets) > settings.publisher_access.max_records_per_run:
        raise HTTPException(
            status_code=422,
            detail=(
                "La demande dépasse la limite configurée de "
                f"{settings.publisher_access.max_records_per_run} notices."
            ),
        )
    records, missing = database.publisher_records_for_targets(payload.targets)
    if missing:
        sample = ", ".join(missing[:5])
        raise HTTPException(
            status_code=404,
            detail=f"Notices introuvables ({len(missing)}) : {sample}",
        )
    run_id = database.create_publisher_access_run(
        profile_id=profile.id,
        authorization_reference=payload.authorization_reference.strip(),
        record_ids=[str(record["id"]) for record in records],
    )
    background_tasks.add_task(
        PublisherCollectionService(settings, database).run,
        run_id=run_id,
        profile_id=profile.id,
        records=records,
    )
    return {"run_id": run_id, "state": "queued", "target_count": len(records)}


@router.get("/runs/{run_id}")
def publisher_collection_run(
    run_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, Any]:
    _require_enabled(settings)
    run = database.publisher_access_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Collecte éditeur inconnue.")
    return run
