"""Explicit user actions for downloading and preparing common-corpus updates."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.config import Settings
from app.corpus_packages.installer import (
    CorpusInstallError,
    ReadyCorpusUpdate,
    ValidatedCorpusPackage,
    extract_staged_package,
    mark_update_ready,
    stage_available_package,
    validate_extracted_corpus,
    verify_staged_package,
)


def validated_update_path(settings: Settings) -> Path:
    return settings.paths.cache_dir / "corpus-updates" / "validated.json"


def save_validated_update(
    settings: Settings,
    validated: ValidatedCorpusPackage,
) -> Path:
    """Persist the completed download phase without scheduling activation."""

    destination = validated_update_path(settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".validated.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(
            json.dumps(validated.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def load_validated_update(settings: Settings) -> ValidatedCorpusPackage:
    """Reload and revalidate a locally downloaded update before it becomes ready."""

    marker = validated_update_path(settings)
    if not marker.is_file():
        raise CorpusInstallError("Aucune mise à jour téléchargée et vérifiée n'est disponible.")
    try:
        validated = ValidatedCorpusPackage.model_validate_json(marker.read_bytes())
        staging = Path(validated.staging_directory).resolve()
        extracted = Path(validated.extracted_directory).resolve()
        expected_root = (settings.paths.cache_dir / "corpus-updates").resolve()
        if (
            not staging.is_dir()
            or not staging.is_relative_to(expected_root)
            or not extracted.is_dir()
            or not extracted.is_relative_to(staging)
        ):
            raise CorpusInstallError("La mise à jour téléchargée n'est plus disponible.")
        return validate_extracted_corpus(settings, validated)
    except (OSError, ValueError) as exc:
        if isinstance(exc, CorpusInstallError):
            raise
        raise CorpusInstallError("Le marqueur de mise à jour téléchargée est invalide.") from exc


def download_and_validate_available_update(settings: Settings) -> ValidatedCorpusPackage:
    """Copy, verify, extract and query the available update without activating it."""

    staged = stage_available_package(settings)
    verify_staged_package(settings, staged)
    extracted = extract_staged_package(settings, staged)
    validated = validate_extracted_corpus(settings, extracted)
    save_validated_update(settings, validated)
    return validated


def mark_validated_update_ready(settings: Settings) -> ReadyCorpusUpdate:
    """Schedule an already validated download for the next application startup."""

    return mark_update_ready(settings, load_validated_update(settings))
