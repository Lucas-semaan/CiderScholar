"""Closed corpus scopes and framework-independent access rules."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

LOCAL_PROFILE_ENV = "CIDERSCHOLAR_LOCAL_PROFILE"


class CorpusScope(StrEnum):
    """Origin of persisted scientific material."""

    COMMON = "common"
    PRIVATE = "private"


class LocalProfile(StrEnum):
    """Local authorization profile; never inferred from request input."""

    USER = "user"
    ADMIN = "admin"


class CorpusMutationForbiddenError(PermissionError):
    """The local profile cannot mutate the requested corpus scope."""


def corpus_scope_label(scope: CorpusScope) -> str:
    """Return the stable French label used in citations and exports."""

    return "Corpus commun" if scope is CorpusScope.COMMON else "Document privé"


@dataclass(frozen=True, slots=True)
class CorpusPaths:
    scope: CorpusScope
    root: Path
    pdf_dir: Path
    extracted_dir: Path
    database_path: Path
    qdrant_dir: Path


def authorize_corpus_mutation(scope: CorpusScope, profile: LocalProfile) -> None:
    """Users own private material; only the local admin may mutate common data."""

    if scope is CorpusScope.COMMON and profile is not LocalProfile.ADMIN:
        raise CorpusMutationForbiddenError(
            "Le corpus commun est en lecture seule sur ce profil utilisateur."
        )


def load_local_profile(environ: Mapping[str, str] | None = None) -> LocalProfile:
    """Read the non-distributed machine profile, defaulting safely to user."""

    values = os.environ if environ is None else environ
    raw_profile = values.get(LOCAL_PROFILE_ENV, LocalProfile.USER.value).strip().casefold()
    try:
        return LocalProfile(raw_profile)
    except ValueError as exc:
        raise RuntimeError(f"{LOCAL_PROFILE_ENV} must be 'user' or 'admin'") from exc


def corpus_paths(settings: Settings, scope: CorpusScope) -> CorpusPaths:
    """Resolve one isolated corpus without consulting a web request."""

    paths = settings.paths
    if scope is CorpusScope.COMMON:
        return CorpusPaths(
            scope=scope,
            root=paths.common_dir,
            pdf_dir=paths.common_pdf_dir,
            extracted_dir=paths.common_extracted_dir,
            database_path=paths.common_database_path,
            qdrant_dir=paths.common_qdrant_dir,
        )
    return CorpusPaths(
        scope=scope,
        root=paths.private_dir,
        pdf_dir=paths.private_pdf_dir,
        extracted_dir=paths.private_extracted_dir,
        database_path=paths.private_database_path,
        qdrant_dir=paths.private_qdrant_dir,
    )


def settings_for_corpus(settings: Settings, scope: CorpusScope) -> Settings:
    """Reuse corpus workflows with paths confined to one scope."""

    selected = corpus_paths(settings, scope)
    paths = settings.paths.model_copy(
        update={
            "pdf_dir": selected.pdf_dir,
            "extracted_dir": selected.extracted_dir,
            "database_path": selected.database_path,
            "qdrant_dir": selected.qdrant_dir,
        }
    )
    return settings.model_copy(deep=True, update={"paths": paths})
