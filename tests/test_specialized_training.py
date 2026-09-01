from __future__ import annotations

from app.database.sqlite import Database
from app.evaluation.ciderqa import CiderQAEvidence, CiderQAQuestion, CiderQASplitDataset
from app.retrieval.specialized_training import (
    SemanticGradeJudgment,
    build_specialized_reranker_dataset,
    ciderqa_training_examples,
    load_specialized_reranker_dataset,
    semantic_grade_training_examples,
    write_specialized_reranker_dataset,
)


def _dataset(split: str = "development") -> CiderQASplitDataset:
    return CiderQASplitDataset(
        schema_version=1,
        split=split,
        questions=[
            CiderQAQuestion(
                schema_version=1,
                id="ciderqa-training-01",
                family_id="family-training-01",
                split=split,
                language="fr",
                task="direct",
                question="Quel effet le cuvage a-t-il sur le rendement ?",
                answerable=True,
                expected_answer="Le passage de référence documente une variation.",
                expected_claims=["Le rendement varie dans les conditions étudiées."],
                reference_evidence=[
                    CiderQAEvidence(
                        id="evidence-training-01",
                        notice_id="notice-1",
                        article_id="article-1",
                        fragment_id="fragment-1",
                        article_sha256="a" * 64,
                        kind="body",
                        page_start=2,
                        page_end=2,
                        excerpt="Pre-press holding changed juice yield in the tested apples.",
                    )
                ],
            )
        ],
    )


def test_specialized_dataset_combines_ciderqa_and_sqlite_hydrated_a_d(settings, tmp_path) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "article-grade",
            "sha256": "e" * 64,
            "doi": None,
            "title": "Negative retrieval example",
            "abstract": "Finished cider storage.",
            "authors": [],
            "journal": None,
            "publication_year": 2025,
            "language": "en",
            "pdf_path": "data/pdf/grade.pdf",
            "validation_status": "indexed",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": 2,
                "page_end": 2,
                "chunk_index": 0,
                "text": "Finished cider storage did not study apple pre-press holding.",
                "token_count": 9,
                "embedding_status": "indexed",
            }
        ],
    )
    chunk_id = database.article_chunk_ids("article-grade")[0]
    judgments = [
        SemanticGradeJudgment(
            decision_id="decision-grade-1",
            question="Quel effet le cuvage a-t-il sur le rendement ?",
            article_id="article-grade",
            chunk_id=chunk_id,
            grade="D",
            rationale="Le passage traite du stockage du produit fini.",
        )
    ]

    dataset = build_specialized_reranker_dataset(
        ciderqa_training_examples(_dataset()),
        semantic_grade_training_examples(database, judgments),
    )
    destination = write_specialized_reranker_dataset(dataset, tmp_path / "training.jsonl")
    loaded = load_specialized_reranker_dataset(destination)

    assert [example.grade for example in loaded.examples] == ["A", "D"]
    assert loaded.examples[1].document == (
        "Finished cider storage did not study apple pre-press holding."
    )
    assert loaded.examples[1].target_score == 0.0
