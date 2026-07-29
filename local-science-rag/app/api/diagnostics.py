"""Read-only, content-free readiness diagnostics."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_settings
from app.config import Settings
from app.diagnostics import build_readiness_report

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/readiness")
def readiness(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
    return build_readiness_report(settings)
