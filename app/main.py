"""FastAPI application factory bound to localhost by documented launch commands."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.admin.secrets import AdminBibliographicKeyVault
from app.api.admin_maintenance import router as admin_maintenance_router
from app.api.argo_key import router as argo_key_router
from app.api.chatbot import router as chatbot_router
from app.api.corpus_updates import router as corpus_updates_router
from app.api.diagnostics import router as diagnostics_router
from app.api.errors import install_error_handlers
from app.api.health import router as health_router
from app.api.ingestion import router as ingestion_router
from app.api.jobs import router as jobs_router
from app.api.library import router as library_router
from app.api.onboarding import router as onboarding_router
from app.api.pilot_feedback import router as pilot_feedback_router
from app.api.publisher_access import router as publisher_access_router
from app.api.suggestions import router as suggestions_router
from app.api.synthesis import router as synthesis_router
from app.api.system import router as system_router
from app.config import Settings, configured_secret_names, load_settings
from app.corpora import load_local_profile
from app.corpus_packages.activation import (
    activate_ready_update_at_startup,
    apply_scheduled_rollback_at_startup,
)
from app.corpus_packages.checks import refresh_corpus_update_if_due
from app.database.sqlite import Database
from app.desktop.app_updates import check_application_update
from app.secrets import hydrate_user_environment
from app.suggestions.packaging import retry_pending_packages


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or load_settings()
    hydrate_user_environment(configured_secret_names(resolved_settings))
    AdminBibliographicKeyVault(
        resolved_settings,
        load_local_profile(),
    ).hydrate_process_environment()
    database = Database(resolved_settings.paths.database_path)
    common_corpus_database = Database(resolved_settings.paths.common_database_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        rollback = apply_scheduled_rollback_at_startup(resolved_settings)
        if rollback is None:
            activate_ready_update_at_startup(resolved_settings)
        resolved_settings.paths.create()
        database.initialize()
        common_corpus_database.initialize()
        application.state.application_update = check_application_update(resolved_settings)
        refresh_corpus_update_if_due(resolved_settings)
        retry_pending_packages(resolved_settings)
        yield

    application = FastAPI(
        title="Local Science RAG",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.database = database
    application.state.common_corpus_database = common_corpus_database
    application.state.admin_maintenance_deferred = False
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )
    install_error_handlers(application)
    application.include_router(health_router)
    application.include_router(admin_maintenance_router)
    application.include_router(argo_key_router)
    application.include_router(chatbot_router)
    application.include_router(corpus_updates_router)
    application.include_router(diagnostics_router)
    application.include_router(system_router)
    application.include_router(ingestion_router)
    application.include_router(jobs_router)
    application.include_router(library_router)
    application.include_router(onboarding_router)
    application.include_router(pilot_feedback_router)
    application.include_router(publisher_access_router)
    application.include_router(synthesis_router)
    application.include_router(suggestions_router)
    mount_frontend(application)
    return application


def mount_frontend(application: FastAPI) -> None:
    """Serve the compiled Tailwind SPA without weakening the API boundary."""

    frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    index_path = frontend_dist / "index.html"
    assets_path = frontend_dist / "assets"
    if not index_path.is_file():
        return
    if assets_path.is_dir():
        application.mount("/assets", StaticFiles(directory=assets_path), name="frontend-assets")

    @application.get("/{frontend_path:path}", include_in_schema=False)
    def frontend(frontend_path: str) -> FileResponse:
        if frontend_path.startswith("api/") or frontend_path == "api":
            raise HTTPException(status_code=404, detail="API route not found")
        requested = (frontend_dist / frontend_path).resolve()
        if frontend_path and requested.is_file():
            try:
                requested.relative_to(frontend_dist.resolve())
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="File not found") from exc
            return FileResponse(requested)
        return FileResponse(index_path)


app = create_app()
