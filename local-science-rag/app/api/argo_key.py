"""Local-only ARGO key configuration routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_settings
from app.api.schemas import ArgoKeyRequest
from app.config import Settings
from app.llm.argo_client import ArgoClient, clear_model_validation_cache
from app.llm.argo_key import (
    ArgoConnectionStatus,
    ArgoKeyStatus,
    ArgoKeyStore,
    argo_connection_status,
)

router = APIRouter(prefix="/api/argo-key", tags=["argo-key"])


@router.get("", response_model=ArgoKeyStatus)
def argo_key_status(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ArgoKeyStatus:
    return ArgoKeyStore(settings).status()


@router.put("", response_model=ArgoKeyStatus)
def save_argo_key(
    payload: ArgoKeyRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ArgoKeyStatus:
    ArgoKeyStore(settings).save(payload.key)
    clear_model_validation_cache()
    return ArgoKeyStatus(configured=True)


@router.delete("", response_model=ArgoKeyStatus)
def delete_argo_key(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ArgoKeyStatus:
    ArgoKeyStore(settings).delete()
    clear_model_validation_cache()
    return ArgoKeyStatus(configured=False)


@router.post("/test", response_model=ArgoConnectionStatus)
def test_argo_key_connection(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ArgoConnectionStatus:
    key = ArgoKeyStore(settings).load()
    if key is None:
        return argo_connection_status(key_configured=False, health=None)
    with ArgoClient(settings, api_key=key or "") as client:
        health = client.health()
    return argo_connection_status(key_configured=True, health=health)
