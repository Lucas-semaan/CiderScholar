"""Crash-safe local packaging and atomic OneDrive/SharePoint handoff."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.corpus_packages.distribution import (
    create_distribution_layout,
    validate_distribution_root,
)
from app.suggestions.models import (
    PdfSuggestionSource,
    PreparedSuggestionPackage,
    SuggestionArgoDecision,
    SuggestionArtifact,
    SuggestionCandidateContext,
    SuggestionDraft,
    SuggestionPackage,
    SuggestionReceipt,
)
from app.suggestions.validation import canonical_package_hash


class DuplicateSuggestionError(ValueError):
    """The same DOI or PDF was already submitted from this profile."""


class SuggestionPackageError(RuntimeError):
    """A local package or synchronized handoff is invalid."""


def suggestion_outbox(settings: Settings) -> Path:
    return settings.paths.cache_dir / "suggestion-outbox"


def suggestion_receipts(settings: Settings) -> Path:
    return settings.paths.data_dir / "suggestion-receipts"


def deduplication_keys(
    draft: SuggestionDraft,
    candidate: SuggestionCandidateContext,
) -> set[str]:
    keys: set[str] = set()
    if candidate.doi:
        keys.add(f"doi:{candidate.doi}")
    if isinstance(draft.source, PdfSuggestionSource):
        keys.add(f"pdf:{draft.source.sha256}")
    return keys


def _key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _receipt_path(settings: Settings, key: str) -> Path:
    return suggestion_receipts(settings) / f"{_key_digest(key)}.json"


def _package_files(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def package_hash(directory: Path) -> str:
    return canonical_package_hash(_package_files(directory))


def ensure_not_duplicate(
    settings: Settings,
    draft: SuggestionDraft,
    candidate: SuggestionCandidateContext,
) -> None:
    keys = deduplication_keys(draft, candidate)
    if any(_receipt_path(settings, key).is_file() for key in keys):
        raise DuplicateSuggestionError("Ce DOI ou ce PDF a déjà été proposé depuis ce poste.")
    root = suggestion_outbox(settings)
    for manifest_path in root.glob("*/suggestion.json"):
        try:
            package = SuggestionPackage.model_validate_json(manifest_path.read_bytes())
            pending_draft = SuggestionDraft(
                suggestion_id=package.suggestion_id,
                created_at=package.created_at,
                source=package.source,
                scientific_comment=package.scientific_comment,
            )
            if keys & deduplication_keys(pending_draft, package.candidate):
                raise DuplicateSuggestionError(
                    "Ce DOI ou ce PDF est déjà en attente de transmission."
                )
        except (OSError, ValueError) as exc:
            if isinstance(exc, DuplicateSuggestionError):
                raise


def build_suggestion_package(
    settings: Settings,
    draft: SuggestionDraft,
    candidate: SuggestionCandidateContext,
    decision: SuggestionArgoDecision,
    *,
    pdf_path: Path | None = None,
) -> PreparedSuggestionPackage:
    """Create a complete package locally before it can become visible in the inbox."""

    root = suggestion_outbox(settings)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / str(draft.suggestion_id)
    if destination.exists():
        raise DuplicateSuggestionError("Cette suggestion possède déjà un paquet local.")
    temporary = root / f".tmp-{uuid.uuid4().hex}"
    artifacts: list[SuggestionArtifact] = []
    try:
        temporary.mkdir()
        if isinstance(draft.source, PdfSuggestionSource):
            if pdf_path is None or not pdf_path.is_file():
                raise SuggestionPackageError("Le PDF validé n'est plus disponible.")
            payload = pdf_path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != draft.source.sha256:
                raise SuggestionPackageError("Le PDF a changé après sa validation.")
            target = temporary / draft.source.internal_filename
            shutil.copy2(pdf_path, target)
            artifacts.append(
                SuggestionArtifact(
                    filename=target.name,
                    size_bytes=target.stat().st_size,
                    sha256=draft.source.sha256,
                )
            )
        manifest = SuggestionPackage(
            suggestion_id=draft.suggestion_id,
            created_at=draft.created_at,
            source=draft.source,
            scientific_comment=draft.scientific_comment,
            candidate=candidate.model_copy(update={"text_excerpt": None}),
            decision=decision,
            artifacts=artifacts,
        )
        (temporary / "suggestion.json").write_text(
            manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        digest = package_hash(temporary)
        temporary.replace(destination)
        return PreparedSuggestionPackage(
            directory=str(destination),
            package_sha256=digest,
            manifest=manifest,
        )
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def load_prepared_package(directory: Path) -> PreparedSuggestionPackage:
    manifest_path = directory / "suggestion.json"
    try:
        manifest = SuggestionPackage.model_validate_json(manifest_path.read_bytes())
        if directory.name != str(manifest.suggestion_id):
            raise SuggestionPackageError("Le répertoire ne correspond pas à la suggestion.")
        expected = {artifact.filename: artifact for artifact in manifest.artifacts}
        actual = {
            path.name: path
            for path in directory.iterdir()
            if path.is_file() and path.name != "suggestion.json"
        }
        if set(actual) != set(expected):
            raise SuggestionPackageError("La liste des artefacts de suggestion est invalide.")
        for name, artifact in expected.items():
            payload = actual[name].read_bytes()
            if (
                len(payload) != artifact.size_bytes
                or hashlib.sha256(payload).hexdigest() != artifact.sha256
            ):
                raise SuggestionPackageError(f"Hash de l'artefact invalide : {name}")
        return PreparedSuggestionPackage(
            directory=str(directory),
            package_sha256=package_hash(directory),
            manifest=manifest,
        )
    except (OSError, ValueError) as exc:
        if isinstance(exc, SuggestionPackageError):
            raise
        raise SuggestionPackageError("Le paquet local est invalide.") from exc


def _write_receipts(settings: Settings, prepared: PreparedSuggestionPackage) -> None:
    draft = SuggestionDraft(
        suggestion_id=prepared.manifest.suggestion_id,
        created_at=prepared.manifest.created_at,
        source=prepared.manifest.source,
        scientific_comment=prepared.manifest.scientific_comment,
    )
    receipt = SuggestionReceipt(
        suggestion_id=prepared.manifest.suggestion_id,
        submitted_at=datetime.now(UTC),
        package_sha256=prepared.package_sha256,
    )
    root = suggestion_receipts(settings)
    root.mkdir(parents=True, exist_ok=True)
    for key in deduplication_keys(draft, prepared.manifest.candidate):
        destination = _receipt_path(settings, key)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(receipt.model_dump_json(indent=2) + "\n", encoding="utf-8")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


def transmit_prepared_package(
    settings: Settings,
    prepared: PreparedSuggestionPackage,
) -> Path:
    """Atomically expose a complete package, then retain only minimal local receipts."""

    source = Path(prepared.directory).resolve()
    expected_root = suggestion_outbox(settings).resolve()
    if not source.is_dir() or not source.is_relative_to(expected_root):
        raise SuggestionPackageError("Le paquet sort du dossier d'attente local.")
    prepared = load_prepared_package(source)
    distribution = validate_distribution_root(settings)
    inbox = create_distribution_layout(distribution).suggestions_inbox
    destination = inbox / str(prepared.manifest.suggestion_id)
    if destination.exists():
        if package_hash(destination) != prepared.package_sha256:
            raise SuggestionPackageError("Un paquet distant différent porte déjà cet identifiant.")
    else:
        staging = inbox / f".s-{uuid.uuid4().hex}"
        try:
            shutil.copytree(source, staging)
            if package_hash(staging) != prepared.package_sha256:
                raise SuggestionPackageError("La copie synchronisée diffère du paquet local.")
            staging.replace(destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    _write_receipts(settings, prepared)
    shutil.rmtree(source)
    return destination


def retry_pending_packages(settings: Settings) -> int:
    """Retry complete local packages once; unavailable synchronization remains non-fatal."""

    transmitted = 0
    for directory in sorted(suggestion_outbox(settings).glob("*")):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        try:
            transmit_prepared_package(settings, load_prepared_package(directory))
        except (OSError, ValueError, RuntimeError):
            continue
        transmitted += 1
    return transmitted
