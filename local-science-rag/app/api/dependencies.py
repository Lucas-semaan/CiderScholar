"""Typed FastAPI dependencies for application state."""

from __future__ import annotations

from fastapi import Request

from app.config import Settings
from app.database.sqlite import Database


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_common_corpus_database(request: Request) -> Database:
    return request.app.state.common_corpus_database


def get_private_corpus_database(request: Request) -> Database:
    return request.app.state.private_corpus_database
