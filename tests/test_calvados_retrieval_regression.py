from __future__ import annotations

from app.models.chatbot import ChatEvidencePassage, ChatEvidenceRecord
from app.retrieval.scientific_intent import (
    ScientificFacet,
    ScientificIntent,
    analyze_scientific_intent,
    score_scientific_text,
)
from app.services.workflows import (
    merge_chat_evidence,
    rerank_bibliographic_candidates,
)
from app.updates.vector_index import BibliographicHybridResult

QUESTION = "impacte de l'élevage en barrique sur les aromes et la structure des calvados"
EXPECTED_DIRECT_DOIS = {
    "10.1021/jf034280o",
    "10.1021/jf0347618",
    "10.1016/j.foodchem.2024.138390",
    "10.1016/j.foodchem.2020.126643",
}


def _candidate(
    rank: int,
    *,
    title: str,
    abstract: str,
    doi: str,
    full_text: bool = False,
) -> BibliographicHybridResult:
    prefix = "common:" if full_text else "common-abstract:"
    return BibliographicHybridResult(
        rank=rank,
        record_id=f"{prefix}record-{rank}",
        title=title,
        abstract=abstract,
        authors=["Ada Test"],
        journal="Food Chemistry",
        publication_year=2024,
        doi=doi,
        url=f"https://doi.org/{doi}",
        sources=["local"],
        lexical_rank=rank,
        vector_rank=rank,
        score=0.01,
    )


def _direct_candidates() -> list[BibliographicHybridResult]:
    return [
        _candidate(
            20,
            title=(
                "Influence of Distillation System, Oak Wood Type, and Aging Time "
                "on Volatile Compounds of Cider Brandy"
            ),
            abstract=(
                "Oak aging changed esters, acids, aldehydes and oak lactones "
                "during maturation of cider brandy."
            ),
            doi="10.1021/jf034280o",
            full_text=True,
        ),
        _candidate(
            21,
            title=(
                "Influence of Distillation System, Oak Wood Type, and Aging Time "
                "on Composition of Cider Brandy in Phenolic and Furanic Compounds"
            ),
            abstract=(
                "Phenolic and furanic compounds, color and taste changed during "
                "maturation in French and American oak casks."
            ),
            doi="10.1021/jf0347618",
            full_text=True,
        ),
        _candidate(
            22,
            title=(
                "Chemical characterization and sensory properties of apple brandies "
                "aged with different toasted oak chips"
            ),
            abstract=(
                "Toasted oak chips increased total acidity, volatile acidity, "
                "phenolic compounds, aromatic esters, desirable color and taste."
            ),
            doi="10.1016/j.foodchem.2024.138390",
        ),
        _candidate(
            23,
            title=(
                "Volatile and phenolic profiles of traditional Romanian apple brandy "
                "after rapid ageing with different wood chips"
            ),
            abstract=(
                "Wood chips changed esters, catechin, rutin, vanillin and the "
                "volatile and phenolic profiles of apple brandy."
            ),
            doi="10.1016/j.foodchem.2020.126643",
        ),
    ]


def _evidence(record: BibliographicHybridResult) -> ChatEvidenceRecord:
    return ChatEvidenceRecord(
        record_id=record.record_id,
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title=record.title,
        authors=record.authors,
        doi=record.doi,
        journal=record.journal,
        publication_year=record.publication_year,
        providers=record.sources,
        url=record.url,
        score=record.score,
        passages=[
            ChatEvidencePassage(
                evidence_id=f"{record.record_id}:abstract",
                section="abstract",
                text=record.abstract,
            )
        ],
    )


def test_calvados_intent_decomposes_matrix_process_and_answer_facets() -> None:
    intent = analyze_scientific_intent(QUESTION)

    assert intent.matrix_primary == ["calvados"]
    assert {"apple brandy", "apple spirit", "cider brandy"} <= set(intent.matrix_close)
    assert "oak ageing" in intent.process_terms
    assert {facet.key for facet in intent.facets} == {
        "aroma",
        "structure",
        "evolution",
    }


def test_calvados_direct_articles_beat_lexically_close_wine_articles() -> None:
    intent = analyze_scientific_intent(QUESTION)
    direct = score_scientific_text(
        intent,
        title="Apple brandy aged with toasted oak chips",
        text="Volatile esters, phenolic compounds, acidity, color and mouthfeel were measured.",
    )
    wine = score_scientific_text(
        intent,
        title="Oak barrel fermentation of white wine",
        text="Furfural and aroma compounds were measured during wine fermentation.",
    )

    assert direct.causal_match is True
    assert direct.matrix_tier == "near"
    assert direct.score > wine.score
    assert wine.matrix_tier == "distant"


def test_title_matrix_blocks_incidental_calvados_upgrade() -> None:
    intent = analyze_scientific_intent(QUESTION)
    result = score_scientific_text(
        intent,
        title="Profiling the Aroma of Grape Spirits for Port Wine",
        text=(
            "Calvados is mentioned as background. The study measures volatile "
            "compounds and sensory aroma, but does not age apple spirit in oak."
        ),
    )

    assert result.matrix_tier == "distant"


def test_calvados_expected_dois_survive_article_reranking() -> None:
    noise = [
        _candidate(
            index,
            title=f"Oak aroma in white wine {index}",
            abstract="Oak barrel fermentation changed furfural and wine aroma.",
            doi=f"10.1000/wine-{index}",
        )
        for index in range(1, 8)
    ]

    ranked = rerank_bibliographic_candidates(
        QUESTION,
        [*noise, *_direct_candidates()],
        limit=6,
    )

    assert {record.doi for record in ranked[:4]} >= EXPECTED_DIRECT_DOIS


def test_calvados_evidence_merge_covers_facets_before_distant_wine() -> None:
    direct_evidence = [_evidence(record) for record in _direct_candidates()]
    wine_evidence = [
        _evidence(
            _candidate(
                index,
                title=f"Oak aroma in white wine {index}",
                abstract="Wine barrel fermentation changed furfural and sulfur aroma.",
                doi=f"10.1000/wine-{index}",
            )
        )
        for index in range(1, 7)
    ]

    selected = merge_chat_evidence(
        wine_evidence,
        direct_evidence,
        query=QUESTION,
        limit=6,
    )

    assert {record.doi for record in selected[:4]} >= EXPECTED_DIRECT_DOIS
    assert {"aroma", "structure", "evolution"} <= {
        facet for record in selected for facet in record.matched_facets
    }


def test_structured_evidence_prioritizes_a_b_before_c_d_semantic_candidates() -> None:
    intent = ScientificIntent(
        question="How does target process affect target outcome in target matrix?",
        matrix_primary=["target matrix"],
        matrix_close=["close matrix"],
        matrix_distant=["distant matrix"],
        process_terms_en=["target process"],
        excluded_terms=["excluded matrix"],
        facets=[
            ScientificFacet(
                key="outcome",
                label="Target outcome",
                terms_fr=["resultat cible"],
                terms_en=["target outcome"],
            )
        ],
    )
    candidates = [
        _candidate(
            1,
            title="Target matrix target process target outcome",
            abstract="The target process changed the target outcome.",
            doi="10.1000/grade-a",
        ),
        _candidate(
            2,
            title="Close matrix target process target outcome",
            abstract="The target process changed the target outcome.",
            doi="10.1000/grade-b",
        ),
        _candidate(
            3,
            title="Distant matrix target process target outcome",
            abstract="The target process changed the target outcome.",
            doi="10.1000/grade-c",
        ),
        _candidate(
            4,
            title="Excluded matrix target process target outcome",
            abstract="The target process changed the target outcome.",
            doi="10.1000/grade-d",
        ),
    ]

    ranked = rerank_bibliographic_candidates(
        intent.question,
        candidates,
        limit=4,
        intent_override=intent,
    )
    selected = merge_chat_evidence(
        [],
        [_evidence(record) for record in candidates],
        query=intent.question,
        limit=4,
        intent_override=intent,
    )

    assert [record.doi for record in ranked] == [
        "10.1000/grade-a",
        "10.1000/grade-b",
        "10.1000/grade-c",
        "10.1000/grade-d",
    ]
    assert [record.doi for record in selected] == [
        "10.1000/grade-a",
        "10.1000/grade-b",
        "10.1000/grade-c",
        "10.1000/grade-d",
    ]
    assert [record.evidence_grade for record in selected] == ["A", "B", "C", "D"]


def test_unstructured_evidence_keeps_retrieval_fallback() -> None:
    intent = ScientificIntent(question="What does the corpus report?")
    candidates = [
        _candidate(
            1,
            title="First retrieved record",
            abstract="First retained abstract.",
            doi="10.1000/fallback-a",
        ),
        _candidate(
            2,
            title="Second retrieved record",
            abstract="Second retained abstract.",
            doi="10.1000/fallback-b",
        ),
    ]

    ranked = rerank_bibliographic_candidates(
        intent.question,
        candidates,
        limit=2,
        intent_override=intent,
    )
    selected = merge_chat_evidence(
        [],
        [_evidence(record) for record in candidates],
        query=intent.question,
        limit=2,
        intent_override=intent,
    )

    assert [record.doi for record in ranked] == ["10.1000/fallback-a", "10.1000/fallback-b"]
    assert {record.doi for record in selected} == {"10.1000/fallback-a", "10.1000/fallback-b"}
    assert {record.evidence_grade for record in selected} == {"unassessed"}
