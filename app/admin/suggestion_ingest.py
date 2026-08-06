"""Validate, deduplicate, import and archive complete shared suggestion packages."""

from __future__ import annotations

import shutil
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.corpora import CorpusScope, settings_for_corpus
from app.corpus_packages.distribution import create_distribution_layout, validate_distribution_root
from app.database.sqlite import Database
from app.services.workflows import ingest_paths
from app.suggestions.evaluation import decision_is_accepted
from app.suggestions.models import PdfSuggestionSource, SuggestionPackage, UrlSuggestionSource
from app.suggestions.packaging import load_prepared_package, package_hash
from app.updates.harvest import BibliographicHarvestStore
from app.updates.models import BibliographicRecord


class SuggestionImportReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scanned: int = Field(ge=0)
    imported: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    rejected: int = Field(ge=0)
    corrupt: int = Field(ge=0)
    errors: list[str] = Field(default_factory=list, max_length=100)


def _package_keys(package: SuggestionPackage) -> set[str]:
    keys: set[str] = set()
    if package.candidate.doi:
        keys.add(f"doi:{package.candidate.doi.casefold()}")
    if isinstance(package.source, UrlSuggestionSource):
        keys.add(f"url:{package.source.url.casefold()}")
    if isinstance(package.source, PdfSuggestionSource):
        keys.add(f"pdf:{package.source.sha256}")
    return keys


def _existing_keys(database: Database) -> set[str]:
    keys: set[str] = set()
    with closing(database.connect()) as connection:
        for row in connection.execute("SELECT doi, sha256 FROM articles"):
            if row["doi"]:
                keys.add(f"doi:{str(row['doi']).casefold()}")
            if row["sha256"]:
                keys.add(f"pdf:{row['sha256']}")
        for row in connection.execute("SELECT doi, url FROM bibliographic_records"):
            if row["doi"]:
                keys.add(f"doi:{str(row['doi']).casefold()}")
            if row["url"]:
                keys.add(f"url:{str(row['url']).casefold()}")
    return keys


def _archive_package(source: Path, archive: Path, outcome: str) -> None:
    destination = archive / "suggestions" / outcome / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if package_hash(destination) == package_hash(source):
            shutil.rmtree(source)
            return
        raise RuntimeError("Une suggestion archivée différente porte déjà cet UUID.")
    source.replace(destination)


def _import_reference(
    settings: Settings,
    database: Database,
    package: SuggestionPackage,
) -> bool:
    title = package.candidate.title
    if not title:
        return False
    store = BibliographicHarvestStore(database)
    theme = package.decision.theme or "suggestion"
    run_id, _ = store.start_run(
        settings,
        themes={theme: "suggestion validée"},
        sources=["Suggestion"],
    )
    source_id = str(package.suggestion_id)
    record_id = store.upsert_hit(
        run_id=run_id,
        theme=theme,
        rank=1,
        record=BibliographicRecord(
            source="Suggestion",
            source_id=source_id,
            title=title,
            abstract=package.candidate.abstract,
            authors=[],
            doi=package.candidate.doi,
            url=(package.source.url if isinstance(package.source, UrlSuggestionSource) else None),
        ),
    )
    store.finish_run(
        run_id=run_id,
        state="completed" if record_id else "failed",
        raw_record_count=1,
        errors=[] if record_id else [{"error_type": "duplicate", "message": "excluded DOI"}],
        completed_at=datetime.now(UTC),
    )
    return record_id is not None


def _import_pdf(
    settings: Settings, common: Database, directory: Path, package: SuggestionPackage
) -> bool:
    source = package.source
    if not isinstance(source, PdfSuggestionSource):
        return False
    target = settings.paths.common_pdf_dir / f"suggestion-{package.suggestion_id}.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    incoming = directory / source.internal_filename
    temporary = target.with_suffix(".pdf.part")
    try:
        shutil.copy2(incoming, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    report = ingest_paths(settings, common, [target])[0]
    return report.status in {"chunks_ready", "duplicate"}


def import_shared_suggestions(settings: Settings) -> SuggestionImportReport:
    root = validate_distribution_root(settings)
    paths = create_distribution_layout(root)
    corpus_settings = settings_for_corpus(settings, CorpusScope.COMMON)
    common = Database(corpus_settings.paths.database_path)
    common.initialize()
    seen = _existing_keys(common)
    counters = {"scanned": 0, "imported": 0, "duplicates": 0, "rejected": 0, "corrupt": 0}
    errors: list[str] = []
    for directory in sorted(paths.suggestions_inbox.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        counters["scanned"] += 1
        try:
            prepared = load_prepared_package(directory)
            package = prepared.manifest
        except (OSError, ValueError, RuntimeError) as exc:
            counters["corrupt"] += 1
            errors.append(f"{directory.name}: {type(exc).__name__}")
            _archive_package(directory, paths.archive, "corrupt")
            continue
        keys = _package_keys(package)
        if keys & seen:
            counters["duplicates"] += 1
            _archive_package(directory, paths.archive, "duplicate")
            continue
        if not decision_is_accepted(package.decision, settings.suggestions.acceptance_threshold):
            counters["rejected"] += 1
            _archive_package(directory, paths.archive, "rejected")
            continue
        try:
            imported = (
                _import_pdf(corpus_settings, common, directory, package)
                if isinstance(package.source, PdfSuggestionSource)
                else _import_reference(corpus_settings, common, package)
            )
        except Exception as exc:
            imported = False
            errors.append(f"{directory.name}: {type(exc).__name__}")
        if imported:
            counters["imported"] += 1
            seen.update(keys)
            _archive_package(directory, paths.archive, "imported")
        else:
            counters["rejected"] += 1
            _archive_package(directory, paths.archive, "rejected")
    return SuggestionImportReport(**counters, errors=errors[:100])
