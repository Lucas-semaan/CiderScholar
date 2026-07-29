"""Consistent, non-leaking API errors."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

LOGGER = logging.getLogger(__name__)


def install_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(FileNotFoundError)
    async def file_error_handler(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception(
            "Unhandled API error path=%s type=%s", request.url.path, type(exc).__name__
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Une erreur interne est survenue."},
        )
