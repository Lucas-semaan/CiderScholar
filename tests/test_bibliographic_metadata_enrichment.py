import json
from datetime import UTC, datetime

from app.database.sqlite import Database
from app.services.bibliographic_metadata_enrichment import (
    MetadataTarget,
    MetadataUpdate,
    assess_cross_validated_candidate,
    assess_title_candidate,
    build_update,
    preferred_search_title,
    title_similarity,
)
from app.updates.base import BibliographicApiDeferred
from app.updates.models import BibliographicRecord
from scripts.enrich_corpus_metadata import (
    _apply_updates,
    _assert_target_snapshot,
    _build_updates,
    _completed_queries,
    _load_targets,
    _local_manifestation_type,
    _needs_fallback_sources,
    _search_with_short_deferred_retry,
    _web_validated_updates,
)


def _target(**overrides) -> MetadataTarget:
    values = {
        "kind": "article",
        "record_id": "article-1",
        "title": "Polyphenols and aroma compounds in apple cider",
        "doi": None,
        "authors": (),
        "journal": None,
        "work_type": None,
        "publisher": None,
        "publication_year": 2020,
        "pdf_path": None,
        "source": "local",
    }
    values.update(overrides)
    return MetadataTarget(**values)


def _record(**overrides) -> BibliographicRecord:
    values = {
        "source": "Crossref",
        "source_id": "10.1000/cider",
        "title": "Polyphenols and aroma compounds in apple cider",
        "authors": ["Ada Martin"],
        "journal": "Journal of Cider Science",
        "work_type": "journal-article",
        "publisher": "Example Publisher",
        "publication_year": 2021,
        "doi": "10.1000/cider",
        "url": "https://doi.org/10.1000/cider",
    }
    values.update(overrides)
    return BibliographicRecord(**values)


def test_title_match_accepts_print_online_year_difference() -> None:
    assessment = assess_title_candidate(_target(), _record())

    assert assessment.status == "accepted"
    assert assessment.title_similarity == 1.0


def test_title_match_quarantines_conflicting_authors() -> None:
    assessment = assess_title_candidate(
        _target(authors=("Jane Dupont",)),
        _record(authors=["Ada Martin"]),
    )

    assert assessment.status == "review"
    assert "authors" in assessment.reason


def test_cross_validation_requires_same_doi_and_consistent_title() -> None:
    crossref = _record()
    openalex = _record(source="OpenAlex", source_id="W123")

    assert assess_cross_validated_candidate(_target(), crossref, openalex).status == "accepted"
    assert (
        assess_cross_validated_candidate(
            _target(),
            crossref,
            _record(source="OpenAlex", source_id="W456", doi="10.1000/other"),
        ).status
        == "review"
    )


def test_build_update_only_fills_missing_fields() -> None:
    update = build_update(
        _target(journal="Existing journal"),
        _record(),
        method="crossref_title_openalex_doi",
        confidence=0.99,
    )

    assert update is not None
    assert update.fields == {
        "doi": "10.1000/cider",
        "authors": ["Ada Martin"],
        "work_type": "journal-article",
        "publisher": "Example Publisher",
    }


def test_filename_supplies_search_title_when_pdf_title_is_generic() -> None:
    target = _target(
        title="untitled",
        pdf_path="C:/docs/Martin - 2020 - Polyphenols in cider.pdf",
    )

    assert preferred_search_title(target) == "Polyphenols in cider"
    assert title_similarity(preferred_search_title(target), "Polyphenols in cider") == 1.0


def test_temporary_api_errors_remain_resumable() -> None:
    items = [
        {"record_id": "ok", "query": "accepted", "records": []},
        {
            "record_id": "retry",
            "query": "deferred",
            "records": [],
            "error": {"type": "BibliographicApiDeferred"},
        },
    ]

    assert _completed_queries(items) == {("ok", "accepted")}


def test_short_provider_deferral_is_retried_once(monkeypatch) -> None:
    class DeferredOnceClient:
        calls = 0

        def search(self, query: str, limit: int) -> list[BibliographicRecord]:
            self.calls += 1
            if self.calls == 1:
                raise BibliographicApiDeferred(
                    "rate limited",
                    retry_at=datetime.now(UTC),
                    status_code=429,
                )
            return [_record()]

    client = DeferredOnceClient()
    monkeypatch.setattr("scripts.enrich_corpus_metadata.time.sleep", lambda _: None)

    assert _search_with_short_deferred_retry(client, "cider", 5) == [_record()]
    assert client.calls == 2


def test_web_validation_keeps_source_provenance_and_normalizes_doi(tmp_path) -> None:
    path = tmp_path / "web-validations.jsonl"
    path.write_text(
        json.dumps(
            {
                "record_id": "article-1",
                "provider": "Official university repository",
                "provider_id": "TDX:345",
                "source_url": "https://example.test/thesis",
                "fields": {
                    "doi": "https://doi.org/10.1000/CIDER",
                    "authors": ["Ada Martin"],
                    "work_type": "dissertation",
                    "publisher": "Example University",
                    "publication_year": 2020,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    updates = _web_validated_updates(
        [_target(authors=("Existing Author",), publication_year=None)], path
    )

    assert len(updates) == 1
    assert updates[0].provider_id == "TDX:345"
    assert updates[0].source_url == "https://example.test/thesis"
    assert updates[0].fields["doi"] == "10.1000/cider"
    assert updates[0].fields["work_type"] == "dissertation"
    assert "authors" not in updates[0].fields


def test_campaign_excludes_articles_dated_after_2026(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    with database.transaction() as connection:
        fixtures = ((1, 2026), (2, 2027), (3, 2026))
        for index, year in fixtures:
            connection.execute(
                """
                INSERT INTO articles (
                    id, sha256, title, authors, publication_year, pdf_path,
                    validation_status, source
                ) VALUES (?, ?, ?, '[]', ?, ?, 'indexed', 'local')
                """,
                (
                    f"article-{index}-{year}",
                    str(index) * 64,
                    f"Cider document {year}",
                    year,
                    str(settings.paths.pdf_dir / f"managed-{index}.pdf"),
                ),
            )
        connection.execute(
            """
            INSERT INTO ingestion_jobs (pdf_path, sha256, state, article_id)
            VALUES (?, ?, 'chunks_ready', 'article-3-2026')
            """,
            (
                "C:/Users/test/Desktop/Biblio HG/Efficycle/EffiNews.pdf",
                "3" * 64,
            ),
        )
        connection.execute(
            """
            INSERT INTO ingestion_jobs (pdf_path, sha256, state, article_id)
            VALUES (?, ?, 'chunks_ready', 'article-1-2026')
            """,
            (
                "C:/docs/Martin - 2026 - Cider study.pdf",
                "1" * 64,
            ),
        )

    targets, _ = _load_targets(settings.paths.database_path)

    assert {target.record_id for target in targets} == {"article-1-2026"}
    assert targets[0].pdf_path == "C:/docs/Martin - 2026 - Cider study.pdf"

    curated_targets, _ = _load_targets(
        settings.paths.database_path,
        {
            "article-1-2026": "skip_external_lookup",
            "article-2-2027": "validate_and_correct_year",
        },
    )

    assert {target.record_id for target in curated_targets} == {"article-2-2027"}
    assert curated_targets[0].publication_year is None


def test_curated_local_pdf_uses_non_openalex_fallback_when_crossref_misses() -> None:
    target = _target(pdf_path="C:/Users/test/Desktop/Biblio pascal/Martin - 2020 - cider.pdf")

    assert _needs_fallback_sources(target, [])
    assert not _needs_fallback_sources(target, [_record()])


def test_local_poster_is_not_assimilated_to_the_article_with_the_same_title(
    settings, tmp_path
) -> None:
    target = _target(pdf_path="C:/docs/Godoy et al Poster.pdf")

    updates, reviews = _build_updates(
        [target],
        {},
        {},
        {},
        {},
        {target.record_id: [_record()]},
        {},
        settings,
        tmp_path,
    )

    assert _local_manifestation_type(target) == "conference-poster"
    assert reviews == []
    assert len(updates) == 1
    assert updates[0].method == "local_manifestation"
    assert updates[0].fields == {"work_type": "conference-poster"}
    assert (
        _local_manifestation_type(
            _target(
                title="Règlement concernant la présentation et l’étiquetage",
                pdf_path="C:/docs/presentation-etiquetage-lineaire-vin-spiritueux.pdf",
            )
        )
        is None
    )


def test_apply_only_fills_fields_that_are_still_missing(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO articles (
                id, sha256, title, authors, publication_year, pdf_path,
                validation_status, source
            ) VALUES (
                'article-1', ?, 'Cider paper', '["Existing Author"]', 2099,
                ?, 'indexed', 'local'
            )
            """,
            ("a" * 64, str(settings.paths.pdf_dir / "article.pdf")),
        )
    update = MetadataUpdate(
        kind="article",
        record_id="article-1",
        provider="OpenAlex",
        provider_id="W1",
        source_url="https://openalex.org/W1",
        method="openalex_exact_doi",
        confidence=1.0,
        fields={
            "authors": ["Replacement Author"],
            "publication_year": 2020,
            "work_type": "article",
        },
    )

    applied, conflicts = _apply_updates(
        database,
        [update],
        replace_publication_year_ids={"article-1"},
    )

    with database.connect() as connection:
        article = connection.execute(
            "SELECT authors, publication_year, work_type FROM articles WHERE id = 'article-1'"
        ).fetchone()
    assert applied == 1
    assert conflicts == []
    assert article["authors"] == '["Existing Author"]'
    assert article["publication_year"] == 2020
    assert article["work_type"] == "article"


def test_apply_rejects_a_changed_target_snapshot() -> None:
    initial = [_target(record_id="article-1")]

    _assert_target_snapshot(initial, list(initial), expected_count=1)

    try:
        _assert_target_snapshot(
            initial,
            [_target(record_id="article-2")],
            expected_count=1,
        )
    except RuntimeError as exc:
        assert "targets changed" in str(exc).casefold()
    else:
        raise AssertionError("A changed target set must abort the metadata transaction")
