from __future__ import annotations

from app.chat_effort import AnswerEffort
from app.database.sqlite import Database
from app.models.chatbot import ChatEvidencePassage, ChatEvidenceRecord
from app.retrieval.global_semantic_filter import (
    GlobalSemanticDecision,
    GlobalSemanticFilterResult,
)
from app.retrieval.hypothesis_planning import VerificationNeed
from app.services.workflows import (
    HYPOTHESIS_PLAN_CACHE_VERSION,
    _ascocid_wiki_search_options,
    _label_ascocid_wiki_records,
    _prefer_complete_ascocid_wiki_answer,
    search_common_corpus_full_text_evidence,
)
from app.updates.pilot_rag import _apa_reference, _as_bibliographic_result, _evidence_citation
from scripts.index_ascocid_wiki import discover_sources


def _record(record_id: str, *, wiki: bool) -> ChatEvidenceRecord:
    return ChatEvidenceRecord(
        record_id=record_id,
        origin="local_rag",
        evidence_level="full_text",
        scope="common",
        article_id=record_id,
        title="Source.pdf" if wiki else "Scientific source",
        providers=["ascocid_wiki"] if wiki else ["local"],
        passages=[
            ChatEvidencePassage(
                evidence_id=f"{record_id}:chunk:1",
                chunk_id=1,
                page_start=2,
                page_end=2,
                text="A persisted result relevant to cider fermentation.",
            )
        ],
    )


def test_ascocid_search_does_not_use_generated_hypothesis_queries() -> None:
    question = (
        "Quels effets les produits chlorés de nettoyage peuvent-ils avoir sur le goût "
        "de bouchon dans le cidre ?"
    )

    options = _ascocid_wiki_search_options(
        question,
        maximum_variants=8,
        deep=False,
    )

    assert options["search_queries"] == ()
    assert options["dense_queries"] == ()
    assert options["max_query_variants"] == 3
    assert options["max_vector_query_variants"] == 0
    assert options["include_fallback_variants"] is True
    assert options["intent_override"].question == question
    assert HYPOTHESIS_PLAN_CACHE_VERSION == "hypothesis-v4-ascocid-direct-retrieval"


def test_ascocid_search_retrieves_chlorinated_cleaning_cork_taint_evidence(settings) -> None:
    database = Database(settings.paths.common_database_path)
    database.initialize()
    articles = [
        (
            "wiki-cork-taint",
            "Les odeurs défectueuses",
            (
                "Un défaut de rinçage peut laisser des résidus de produits chlorés. "
                "Ils conduisent à des notes de goût de bouchon dues aux chloroanisoles, "
                "issus de la réaction entre un phénol et le chlore."
            ),
        ),
        (
            "wiki-unrelated",
            "Les levures du cidre",
            "Les levures fermentaires transforment les sucres du moût en éthanol.",
        ),
    ]
    for index, (article_id, title, text) in enumerate(articles):
        database.save_article_and_chunks(
            {
                "id": article_id,
                "sha256": str(index + 1) * 64,
                "title": title,
                "authors": ["Ascocid"],
                "pdf_path": f"{article_id}.pdf",
                "source": "local",
                "validation_status": "validated",
            },
            [
                {
                    "section": "Introduction",
                    "page_start": 1,
                    "page_end": 1,
                    "chunk_index": 0,
                    "text": text,
                    "token_count": len(text.split()),
                }
            ],
        )
        database.upsert_ascocid_wiki_document(
            document_id=f"ascocid-{article_id}",
            relative_path=f"3_Fiches/{title}.docx",
            filename=f"{title}.docx",
            source_sha256=str(index + 3) * 64,
            article_id=article_id,
            indexed_file_path=f"{article_id}.pdf",
            indexed_file_sha256=str(index + 1) * 64,
        )
    question = (
        "Quels effets les produits chlorés de nettoyage peuvent-ils avoir sur le goût "
        "de bouchon dans le cidre ?"
    )

    records = search_common_corpus_full_text_evidence(
        settings,
        query=question,
        article_count=2,
        article_ids=database.ascocid_wiki_article_ids(),
        candidate_limit=24,
        prefix_matching=False,
        supplemental=True,
        **_ascocid_wiki_search_options(question, maximum_variants=8, deep=False),
    )

    assert records
    assert records[0].article_id == "wiki-cork-taint"
    assert "chloroanisoles" in records[0].passages[0].text


def _need(need_id: str) -> VerificationNeed:
    return VerificationNeed(
        need_id=need_id,
        claim_to_verify="Vérifier le résultat demandé.",
        evidence_required="Un document pertinent.",
        search_query="cider fermentation result",
    )


def test_concise_answer_keeps_only_fully_covering_ascocid_sources() -> None:
    wiki = _record("wiki-article", wiki=True)
    general = _record("general-article", wiki=False)
    semantic = GlobalSemanticFilterResult(
        question="Quel résultat ?",
        decisions=[
            GlobalSemanticDecision(
                candidate_id=wiki.record_id,
                relevance="direct",
                supported_need_ids=["v1", "v2"],
                rationale="Le document répond aux deux vérifications.",
            ),
            GlobalSemanticDecision(
                candidate_id=general.record_id,
                relevance="direct",
                supported_need_ids=["v1"],
                rationale="La publication répond partiellement.",
            ),
        ],
        selected_candidate_ids=[wiki.record_id, general.record_id],
        model="test-model",
        prompt_tokens=0,
        completion_tokens=0,
    )

    selected = _prefer_complete_ascocid_wiki_answer(
        [wiki, general],
        semantic,
        [_need("v1"), _need("v2")],
        answer_effort=AnswerEffort.CONCISE,
    )

    assert [record.record_id for record in selected] == [wiki.record_id]


def test_incomplete_ascocid_coverage_keeps_general_corpus() -> None:
    wiki = _record("wiki-article", wiki=True)
    general = _record("general-article", wiki=False)
    semantic = GlobalSemanticFilterResult(
        question="Quel résultat ?",
        decisions=[
            GlobalSemanticDecision(
                candidate_id=wiki.record_id,
                relevance="direct",
                supported_need_ids=["v1"],
                rationale="Le document ne couvre qu'une vérification.",
            )
        ],
        selected_candidate_ids=[wiki.record_id, general.record_id],
        model="test-model",
        prompt_tokens=0,
        completion_tokens=0,
    )

    selected = _prefer_complete_ascocid_wiki_answer(
        [wiki, general],
        semantic,
        [_need("v1"), _need("v2")],
        answer_effort=AnswerEffort.CONCISE,
    )

    assert [record.record_id for record in selected] == [wiki.record_id, general.record_id]


def test_ascocid_citations_name_the_original_file() -> None:
    record = _record("wiki-article", wiki=True)

    assert _evidence_citation(record, record.passages) == "(Ascocid — Source.pdf, p. 2)"
    assert _apa_reference(_as_bibliographic_result(record)) == "Ascocid — Source.pdf."


def test_ascocid_registry_labels_existing_sqlite_evidence(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "article-existing",
            "sha256": "a" * 64,
            "title": "Original bibliographic title",
            "authors": ["Researcher"],
            "pdf_path": "source.pdf",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "Relevant source text.",
                "token_count": 4,
            }
        ],
    )
    database.upsert_ascocid_wiki_document(
        document_id="ascocid-wiki-test",
        relative_path="3_Fiches/Source originale.docx",
        filename="Source originale.docx",
        source_sha256="b" * 64,
        article_id="article-existing",
        indexed_file_path="converted.pdf",
        indexed_file_sha256="a" * 64,
    )
    record = _record("common:article-existing", wiki=False).model_copy(
        update={"article_id": "article-existing"}
    )

    labelled = _label_ascocid_wiki_records(database, [record])

    assert database.ascocid_wiki_article_ids() == ["article-existing"]
    assert labelled[0].title == "Source originale.docx"
    assert labelled[0].authors == ["Ascocid"]
    assert labelled[0].providers == ["ascocid_wiki"]


def test_discovery_excludes_organizational_wiki_pages(tmp_path) -> None:
    wiki_dir = tmp_path / "wiki"
    converted_dir = tmp_path / "converted"
    raw_dir = wiki_dir / "8_Bibliographie"
    sources_dir = wiki_dir / "sources"
    raw_dir.mkdir(parents=True)
    sources_dir.mkdir()
    (wiki_dir / "README.md").write_text("Organization", encoding="utf-8")
    (sources_dir / "derived.md").write_text("Derived", encoding="utf-8")
    raw_pdf = raw_dir / "primary.pdf"
    raw_pdf.write_bytes(b"%PDF-test")

    selected, unsupported = discover_sources(wiki_dir, converted_dir)

    assert [item.source_path for item in selected] == [raw_pdf]
    assert unsupported == []
