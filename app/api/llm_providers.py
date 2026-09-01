"""Local-only configuration routes for selectable LLM providers."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_settings
from app.api.schemas import ActiveLlmProviderRequest, LlmProviderUpdateRequest
from app.config import Settings
from app.llm.argo_client import ArgoClient, ArgoError, clear_model_validation_cache
from app.llm.providers import LlmProviderId, LlmProviderProfile, LlmProviderStore

router = APIRouter(prefix="/api/llm-providers", tags=["llm-providers"])


def _payload(store: LlmProviderStore) -> dict[str, Any]:
    active = store.active_provider()
    return {
        "active_provider": active,
        "providers": [profile.model_dump(mode="json") for profile in store.profiles()],
    }


def _safe_error(provider: LlmProviderId, error: Exception) -> HTTPException:
    label = "ARGO" if provider == "argo" else "Le fournisseur personnalisé"
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=503, detail=f"{label} n'est pas accessible.")


@router.get("")
def list_llm_providers(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    return _payload(LlmProviderStore(settings))


@router.put("/active", response_model=LlmProviderProfile)
def activate_llm_provider(
    payload: ActiveLlmProviderRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> LlmProviderProfile:
    store = LlmProviderStore(settings)
    provider = payload.provider
    profile = store.profile(provider)
    if not profile.key_configured or not profile.base_url or not profile.model:
        raise HTTPException(
            status_code=409,
            detail="Ce fournisseur n'est pas entièrement configuré.",
        )
    try:
        with ArgoClient(settings, provider_id=provider) as client:
            health = client.health()
    except (ArgoError, ValueError, RuntimeError) as error:
        raise _safe_error(provider, error) from error
    if not health.reachable or not health.model_available:
        raise HTTPException(
            status_code=409,
            detail="Le fournisseur doit réussir son test avant d'être activé.",
        )
    clear_model_validation_cache()
    return store.activate(provider)


@router.put("/{provider}", response_model=LlmProviderProfile)
def configure_llm_provider(
    provider: LlmProviderId,
    payload: LlmProviderUpdateRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> LlmProviderProfile:
    store = LlmProviderStore(settings)
    try:
        profile = store.configure(
            provider,
            key=payload.key,
            base_url=payload.base_url,
            model=payload.model,
        )
    except (ValueError, RuntimeError) as error:
        raise _safe_error(provider, error) from error
    clear_model_validation_cache()
    return profile


@router.delete("/{provider}", response_model=LlmProviderProfile)
def delete_llm_provider(
    provider: LlmProviderId,
    settings: Annotated[Settings, Depends(get_settings)],
) -> LlmProviderProfile:
    store = LlmProviderStore(settings)
    profile = store.delete(provider)
    clear_model_validation_cache()
    return profile


@router.post("/{provider}/test")
def test_llm_provider(
    provider: LlmProviderId,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    store = LlmProviderStore(settings)
    profile = store.profile(provider)
    if not profile.key_configured or not profile.base_url or not profile.model:
        return {
            "state": "missing",
            "configured": False,
            "provider": provider,
            "message": "Ce fournisseur n'est pas entièrement configuré.",
        }
    try:
        with ArgoClient(settings, provider_id=provider) as client:
            health = client.health()
    except (ArgoError, ValueError, RuntimeError) as error:
        raise _safe_error(provider, error) from error
    ready = health.reachable and health.model_available
    normalized_error = (health.error or "").casefold()
    state = (
        "ready"
        if ready
        else "model_unavailable"
        if health.reachable
        else "rejected"
        if "rejected" in normalized_error
        else "invalid_response"
        if any(marker in normalized_error for marker in ("invalid", "non-json", "non-object"))
        else "network_unavailable"
    )
    return {
        "state": state,
        "configured": True,
        "provider": provider,
        "message": (
            "La clé et le modèle sont accessibles."
            if ready
            else "Le fournisseur répond, mais le modèle configuré n'est pas accessible."
            if state == "model_unavailable"
            else "La clé API a été refusée."
            if state == "rejected"
            else "Le fournisseur a renvoyé une réponse incompatible."
            if state == "invalid_response"
            else "Le fournisseur LLM est inaccessible."
        ),
    }
