from __future__ import annotations

from uuid import uuid4

from app.corpora import CorpusScope, corpus_paths
from app.database.sqlite import Database
from app.models.chatbot import ChatEvidencePassage, ChatEvidenceRecord
from app.retrieval.chat_checkpoint import (
    ChatRetrievalCheckpoint,
    ChatRetrievalCheckpointStore,
)
from app.retrieval.hypothesis_planning import deterministic_hypothesis_plan


def test_chat_retrieval_checkpoint_persists_identities_without_evidence_text(tmp_path) -> None:
    question = "Quels facteurs contrôlent la fermentation du cidre ?"
    private_evidence_text = "UNIQUE SCIENTIFIC PASSAGE THAT MUST REMAIN ONLY IN SQLITE"
    private_title = "UNIQUE TITLE REHYDRATED FROM SQLITE"
    checkpoint = ChatRetrievalCheckpoint.capture(
        retrieval_query=question,
        corpus_fingerprint="a" * 64,
        planning=deterministic_hypothesis_plan(question),
        evidence=[
            ChatEvidenceRecord(
                record_id="common:article-1",
                origin="local_rag",
                evidence_level="full_text",
                scope="common",
                article_id="article-1",
                title=private_title,
                providers=["local"],
                passages=[
                    ChatEvidencePassage(
                        evidence_id="common:article-1:chunk:17",
                        text=private_evidence_text,
                        chunk_id=17,
                        context_role="result",
                        page_start=3,
                        page_end=3,
                    )
                ],
            )
        ],
        external_result_count=0,
        warnings=[],
        timings=[],
        retrieval_traces=[],
    )
    store = ChatRetrievalCheckpointStore(tmp_path / "checkpoints")
    user_message_id = uuid4()
    request = {"message": question, "history": [], "answer_effort": "balanced"}
    fingerprint = store.request_fingerprint(request)

    store.save(
        user_message_id,
        request_fingerprint=fingerprint,
        checkpoint=checkpoint,
    )

    raw_checkpoint = (tmp_path / "checkpoints" / str(user_message_id) / "retrieval.json").read_text(
        encoding="utf-8"
    )
    assert private_evidence_text not in raw_checkpoint
    assert private_title not in raw_checkpoint
    assert '"chunk_id": 17' in raw_checkpoint
    assert store.load(user_message_id, request_fingerprint=fingerprint) == checkpoint
    assert (
        store.load(
            user_message_id,
            request_fingerprint=store.request_fingerprint({"message": "different"}),
        )
        is None
    )


def test_chat_retrieval_checkpoint_rehydrates_prefixed_bibliographic_abstract(settings) -> None:
    database = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO bibliographic_records (
                id, canonical_key, doi, title, abstract, authors, content_hash,
                embedding_status, relevance_status
            ) VALUES (?, ?, ?, ?, ?, '[]', ?, 'indexed', 'accepted')
            """,
            (
                "record-1",
                "doi:10.1000/checkpoint",
                "10.1000/checkpoint",
                "Current bibliographic title",
                "Current SQLite abstract.",
                "a" * 64,
            ),
        )
    question = "Que montre cet abstract ?"
    checkpoint = ChatRetrievalCheckpoint.capture(
        retrieval_query=question,
        corpus_fingerprint="a" * 64,
        planning=deterministic_hypothesis_plan(question),
        evidence=[
            ChatEvidenceRecord(
                record_id="common-abstract:record-1",
                origin="local_rag",
                evidence_level="abstract",
                scope="common",
                title="Stale title",
                passages=[
                    ChatEvidencePassage(
                        evidence_id="common-abstract:record-1:abstract",
                        text="Stale abstract.",
                    )
                ],
            )
        ],
        external_result_count=0,
        warnings=[],
        timings=[],
        retrieval_traces=[],
    )

    hydrated = checkpoint.rehydrate(settings)

    assert len(hydrated) == 1
    assert hydrated[0].record_id == "common-abstract:record-1"
    assert hydrated[0].title == "Current bibliographic title"
    assert hydrated[0].passages[0].evidence_id == "common-abstract:record-1:abstract"
    assert hydrated[0].passages[0].text == "Current SQLite abstract."


def test_chat_retrieval_checkpoint_preserves_structural_chunk_coordinates() -> None:
    question = "Que dit la section résultats ?"
    checkpoint = ChatRetrievalCheckpoint.capture(
        retrieval_query=question,
        corpus_fingerprint="b" * 64,
        planning=deterministic_hypothesis_plan(question),
        evidence=[
            ChatEvidenceRecord(
                record_id="common:article-xml",
                origin="local_rag",
                evidence_level="full_text",
                scope="common",
                article_id="article-xml",
                title="Native article",
                providers=["europe_pmc"],
                passages=[
                    ChatEvidencePassage(
                        evidence_id="common:article-xml:chunk:23",
                        text="A source passage that must not be checkpointed.",
                        chunk_id=23,
                        locator_kind="structural",
                        section_path="Results / Fermentation",
                        paragraph_start=4,
                        paragraph_end=5,
                        xml_id_start="result-4",
                        xml_id_end="result-5",
                    )
                ],
            )
        ],
        external_result_count=0,
        warnings=[],
        timings=[],
        retrieval_traces=[],
    )

    checkpoint_passage = checkpoint.evidence[0].passages[0]
    assert checkpoint_passage.locator_kind == "structural"
    assert checkpoint_passage.section_path == "Results / Fermentation"
    assert checkpoint_passage.paragraph_start == 4
    assert checkpoint_passage.paragraph_end == 5
    assert checkpoint_passage.xml_id_start == "result-4"
    assert checkpoint_passage.xml_id_end == "result-5"

    rehydration_passage = checkpoint.evidence[0].as_rehydration_record().passages[0]
    assert rehydration_passage.locator_kind == "structural"
    assert rehydration_passage.page_start is None


def test_chat_retrieval_checkpoint_rehydrates_current_structural_locator(settings) -> None:
    database = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "native-article",
            "sha256": "c" * 64,
            "title": "Native cider article",
            "pdf_path": "native-source.xml",
            "validation_status": "validated",
            "source": "europe_pmc",
        },
        [
            {
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "Current structural source text.",
                "token_count": 4,
            }
        ],
    )
    asset_id = database.save_article_source_asset(
        article_id="native-article",
        kind="jats_xml",
        file_path="native-source.xml",
        sha256="d" * 64,
        media_type="application/xml",
        byte_count=100,
        provider="europe_pmc",
    )
    chunk_id = int(database.chunks_for_article("native-article", limit=1)[0]["id"])
    database.save_structural_chunk_locator(
        chunk_id=chunk_id,
        asset_id=asset_id,
        section_path="Results / Fermentation",
        paragraph_start=4,
        paragraph_end=5,
        xml_id_start="result-4",
        xml_id_end="result-5",
        span_text="Current structural source text.",
    )
    question = "Que dit la section résultats ?"
    checkpoint = ChatRetrievalCheckpoint.capture(
        retrieval_query=question,
        corpus_fingerprint="c" * 64,
        planning=deterministic_hypothesis_plan(question),
        evidence=[
            ChatEvidenceRecord(
                record_id="common:native-article",
                origin="local_rag",
                evidence_level="full_text",
                scope="common",
                article_id="native-article",
                title="Stale title",
                providers=["local"],
                passages=[
                    ChatEvidencePassage(
                        evidence_id="common:native-article:chunk:1",
                        text="Stale checkpoint text.",
                        chunk_id=chunk_id,
                        locator_kind="structural",
                        section_path="Results / Fermentation",
                        paragraph_start=4,
                        paragraph_end=5,
                    )
                ],
            )
        ],
        external_result_count=0,
        warnings=[],
        timings=[],
        retrieval_traces=[],
    )

    hydrated = checkpoint.rehydrate(settings)

    assert len(hydrated) == 1
    passage = hydrated[0].passages[0]
    assert passage.text == "Current structural source text."
    assert passage.locator_kind == "structural"
    assert passage.page_start is None
    assert passage.section_path == "Results / Fermentation"
    assert passage.paragraph_start == 4
    assert passage.paragraph_end == 5
