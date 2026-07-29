"""Local service health endpoint."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.llm.argo_client import ArgoClient

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "offline_mode": settings.app.offline_mode,
        "network_bind": settings.app.host,
        "database": "configured",
    }


@router.get("/health/llm")
def llm_health(request: Request) -> JSONResponse:
    """Probe ARGO model access without generating text."""

    with ArgoClient(request.app.state.settings) as client:
        result = client.health()
    status_code = 200 if result.reachable and result.model_available else 503
    return JSONResponse(
        status_code=status_code,
        content=result.model_dump(mode="json"),
    )
