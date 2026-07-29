from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.admin.suggestion_ingest import import_shared_suggestions
from app.corpus_packages.distribution import create_distribution_layout
from app.database.sqlite import Database
from app.suggestions.models import (
    DoiSuggestionSource,
    SuggestionArgoDecision,
    SuggestionCandidateContext,
    SuggestionDraft,
)
from app.suggestions.packaging import build_suggestion_package


def _configured(settings, root: Path):
    active = settings.model_copy(deep=True)
    active.distribution.enabled = True
    active.distribution.synchronized_root = root
    create_distribution_layout(root)
    Database(active.paths.database_path).initialize()
    Database(active.paths.common_database_path).initialize()
    return active


def _inbox_package(settings, doi: str, *, uncertainty: str = "low") -> Path:
    draft = SuggestionDraft(
        created_at=datetime.now(UTC),
        source=DoiSuggestionSource(
            doi=doi,
            title="Cider fermentation microbiology",
            abstract="Apple cider fermentation by yeast and bacteria.",
        ),
    )
    candidate = SuggestionCandidateContext(
        title=draft.source.title,
        doi=draft.source.doi,
        abstract=draft.source.abstract,
    )
    prepared = build_suggestion_package(
        settings,
        draft,
        candidate,
        SuggestionArgoDecision(
            relevant=True,
            reason="Pertinence cidricole directe.",
            theme="microbiologie",
            uncertainty=uncertainty,
            confidence=0.95,
        ),
    )
    destination = (
        settings.distribution.synchronized_root / "suggestions" / "inbox" / str(draft.suggestion_id)
    )
    Path(prepared.directory).replace(destination)
    return destination


def test_admin_imports_one_complete_suggestion_and_archives_cross_user_duplicate(
    settings,
    tmp_path,
) -> None:
    active = _configured(settings, tmp_path / "CiderScholar")
    _inbox_package(active, "10.1000/shared")
    _inbox_package(active, "10.1000/shared")

    report = import_shared_suggestions(active)

    assert report.scanned == 2
    assert report.imported == 1
    assert report.duplicates == 1
    assert not list((active.distribution.synchronized_root / "suggestions" / "inbox").iterdir())
    assert (
        len(list((active.distribution.synchronized_root / "archive" / "suggestions").glob("*/*")))
        == 2
    )
    with Database(active.paths.database_path).connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM bibliographic_records WHERE doi = '10.1000/shared'"
            ).fetchone()[0]
            == 1
        )


def test_admin_revalidates_received_decision_and_ignores_partial_directory(
    settings,
    tmp_path,
) -> None:
    active = _configured(settings, tmp_path / "CiderScholar")
    _inbox_package(active, "10.1000/uncertain", uncertainty="high")
    partial = active.distribution.synchronized_root / "suggestions" / "inbox" / ".s-partial"
    partial.mkdir()
    (partial / "suggestion.json").write_text("partial", encoding="utf-8")

    report = import_shared_suggestions(active)

    assert report.scanned == 1
    assert report.rejected == 1
    assert partial.is_dir()
    with Database(active.paths.database_path).connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM bibliographic_records").fetchone()[0] == 0


def test_corrupt_complete_suggestion_is_archived_without_import(settings, tmp_path) -> None:
    active = _configured(settings, tmp_path / "CiderScholar")
    corrupt = active.distribution.synchronized_root / "suggestions" / "inbox" / "bad-package"
    corrupt.mkdir()
    (corrupt / "suggestion.json").write_text("{}", encoding="utf-8")

    report = import_shared_suggestions(active)

    assert report.corrupt == 1
    assert not corrupt.exists()
    assert (
        active.distribution.synchronized_root
        / "archive"
        / "suggestions"
        / "corrupt"
        / "bad-package"
    ).is_dir()
