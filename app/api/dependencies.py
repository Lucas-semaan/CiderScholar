"""Typed FastAPI dependencies for application state."""

from __future__ import annotations

from fastapi import Request

from app.config import Settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_common_corpus_database(request: Request) -> Database:
    return request.app.state.common_corpus_database


def get_common_corpus_settings(request: Request) -> Settings:
    """Return settings whose mutable scientific paths all target the corpus."""

    return settings_for_corpus(request.app.state.settings, CorpusScope.COMMON)
