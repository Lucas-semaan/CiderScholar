from __future__ import annotations

import json

import pytest

import app.updates.pilot_rag as pilot_rag
from app.chat_effort import AnswerEffort
from app.llm.argo_client import (
    ArgoProtocolError,
    ArgoQuotaError,
    ArgoScientificValidationError,
    ScientificValidationReason,
)
from app.llm.contracts import GenerationMetrics, GenerationResponse
from app.llm.response_style import ResponseStyle
from app.models.chatbot import ChatEvidencePassage, ChatEvidenceRecord
from app.retrieval.coverage_assessment import AxisCoverageAssessment
from app.retrieval.scientific_intent import ScientificFacet
from app.updates.pilot_rag import (
    PROMPT_RETRY_HEADROOM_CHARACTERS,
    CiderAbstractRagService,
    CiderEvidenceAnswer,
    CiderEvidenceRagService,
    CitedEvidenceStatement,
    _apa_reference,
    _clean_author_names,
    _PromptBudgetError,
    _reject_internal_process_leaks,
    _render_evidence_answer,
    _renderable_doi,
    _salvage_grounded_evidence_answer,
    _validate_evidence_grounding,
    _validation_correction_message,
    chatbot_citation_anchors,
)
from app.updates.vector_index import BibliographicHybridResult


@pytest.fixture(autouse=True)
def _enable_legacy_correction_scenarios(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Keep regression coverage for the retired multi-attempt correction path.

    Production is intentionally limited to one scientific generation request.
    These scenarios retain coverage of the safe fallback and correction mechanics
    should that policy ever be configured again.
    """

    if request.node.name == "test_evidence_rag_stops_after_reviewer_generation":
        return
    legacy_limits = {
        "test_faceted_evidence_rag_keeps_cited_drafts_and_assembles_them": 4,
        "test_faceted_final_assembly_failure_returns_cited_partial_drafts": 4,
        "test_first_facet_is_corrected_without_preventing_later_facets": 6,
        "test_faceted_assembly_expands_once_when_effort_claim_threshold_is_validated": 4,
        "test_faceted_assembly_reexpands_after_salvage_and_preserves_validated_drafts": 4,
    }
    maximum = legacy_limits.get(request.node.originalname or request.node.name, 10)
    monkeypatch.setattr(pilot_rag, "MAX_SCIENTIFIC_GENERATION_REQUESTS", maximum)
    generation_budget = pilot_rag._GenerationRequestBudget
    monkeypatch.setattr(
        pilot_rag,
        "_GenerationRequestBudget",
        lambda: generation_budget(maximum=maximum),
    )


def _record(record_id: str, doi: str) -> BibliographicHybridResult:
    return BibliographicHybridResult(
        rank=1,
        record_id=record_id,
        title="Cider microbiology",
        abstract="Yeasts and bacteria influence cider fermentation.",
        authors=["Ada Test"],
        journal="Cider Science",
        publication_year=2025,
        doi=doi,
        url=f"https://doi.org/{doi}",
        sources=["OpenAlex"],
        lexical_rank=1,
        vector_rank=1,
        score=0.1,
    )


def _response(content: str) -> GenerationResponse:
    return GenerationResponse(
        model="chat-gpt-oss-20b",
        content=content,
        done_reason="stop",
        metrics=GenerationMetrics(
            total_duration_seconds=0.1,
            load_duration_seconds=0.0,
            prompt_eval_count=50,
            prompt_eval_duration_seconds=0.0,
            eval_count=20,
            eval_duration_seconds=0.1,
        ),
    )


def test_rendered_evidence_citations_open_their_exact_persisted_passages() -> None:
    passage = ChatEvidencePassage(
        evidence_id="common:ascocid:chunk:7",
        chunk_id=7,
        text="Saccharomyces uvarum has an optimum range from 6 to 10 °C.",
        section="Conditions de milieu",
        page_start=4,
        page_end=4,
    )
    record = ChatEvidenceRecord(
        record_id="common:ascocid",
        origin="local_rag",
        evidence_level="full_text",
        scope="common",
        article_id="ascocid",
        title="Le développement des Saccharomyces.docx",
        authors=["Ascocid"],
        providers=["ascocid_wiki"],
        passages=[passage],
    )
    answer = CiderEvidenceAnswer(
        statements=[
            CitedEvidenceStatement(
                statement="La gamme optimale se situe entre 6 et 10 °C.",
                evidence_ids=[passage.evidence_id],
            )
        ],
        limitations=[],
    )
    evidence = {passage.evidence_id: (record, passage)}

    markdown = _render_evidence_answer(
        answer,
        evidence,
        ResponseStyle.PROSE,
        question="À quelle température conduire la fermentation ?",
    )
    anchors = chatbot_citation_anchors(answer, [record])

    assert len(anchors) == 1
    assert anchors[0].source_family == "ascocid_knowledge"
    assert anchors[0].evidence[0].evidence_id == passage.evidence_id
    assert anchors[0].evidence[0].snippet == passage.text
    assert f"](#citation-{anchors[0].citation_id})" in markdown
    assert "Ascocid — Le développement des Saccharomyces.docx, p. 4" in markdown


def test_bounded_evidence_reserves_ranked_records_for_required_axes() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:article-{index}",
            origin="local_rag",
            evidence_level="full_text",
            scope="common",
            article_id=f"article-{index}",
            title=f"Study {index}",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:article-{index}:chunk:1",
                    chunk_id=1,
                    page_start=1,
                    page_end=1,
                    text=f"Documented result for study {index}.",
                )
            ],
        )
        for index in range(12)
    ]

    selected, _evidence = CiderEvidenceRagService(object())._bounded_evidence(
        records,
        axis_candidate_ids={
            "primary_axis": ["common:article-0"],
            "required_axis_b": ["common:article-10"],
            "required_axis_c": ["common:article-11"],
        },
    )

    selected_ids = {record.record_id for record in selected}
    assert "common:article-10" in selected_ids
    assert "common:article-11" in selected_ids
    # Axis hints can reorder records, but cannot silently discard relevant RAG evidence.
    assert len(selected) == len(records)
    assert selected_ids == {record.record_id for record in records}


def test_deep_prompt_budget_preserves_all_essential_evidence_and_long_axis_drafts() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:article-{index}",
            origin="local_rag",
            evidence_level="full_text",
            scope="common",
            article_id=f"article-{index}",
            title=f"Study {index}",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:article-{index}:chunk:{passage}",
                    chunk_id=passage,
                    page_start=passage,
                    page_end=passage + 1,
                    text=(f"Documented result {index}-{passage}. " + "evidence " * 130),
                )
                for passage in (1, 2)
            ],
        )
        for index in range(36)
    ]
    service = CiderEvidenceRagService(
        object(),
        answer_effort=AnswerEffort.DEEP,
        max_input_characters=64_000,
    )
    _selected, evidence = service._bounded_evidence(records)
    assert len(evidence) == 72
    cited_by_axis = [
        "common:article-0:chunk:1",
        "common:article-2:chunk:1",
        "common:article-4:chunk:1",
        "common:article-6:chunk:1",
    ]
    payload = {
        "question": "Compare all documented axes.",
        "conversation_history": [
            {"role": "user", "content": "context " * 500},
            {"role": "assistant", "content": "prior answer " * 500},
        ],
        "evidence": evidence,
        "facet_drafts": [
            {
                "key": f"axis-{index}",
                "label": f"Axis {index}",
                "query": "documented axis",
                "answer_markdown": "Validated cited draft. " * 600,
                "cited_evidence_ids": [evidence_id],
                "source_record_ids": [f"common:article-{index * 2}"],
            }
            for index, evidence_id in enumerate(cited_by_axis)
        ],
    }
    system = "Scientific instructions. " * 200

    all_evidence_ids = [str(item["evidence_id"]) for item in evidence]
    fitted = service._fit_prompt_payload(
        system,
        payload,
        priority_evidence_ids=cited_by_axis,
        essential_evidence_ids=all_evidence_ids,
    )

    serialized = json.dumps(fitted, ensure_ascii=False)
    assert len(system) + len(serialized) <= 64_000 - PROMPT_RETRY_HEADROOM_CHARACTERS
    assert len(system) + len(serialized) + PROMPT_RETRY_HEADROOM_CHARACTERS <= 64_000
    fitted_by_id = {item["evidence_id"]: item for item in fitted["evidence"]}
    assert set(fitted_by_id) == set(all_evidence_ids)
    assert list(fitted_by_id)[:4] == cited_by_axis
    for evidence_id in cited_by_axis:
        item = fitted_by_id[evidence_id]
        assert item["record_id"]
        assert item["page_start"] is not None
        assert item["page_end"] is not None
        assert item["text"]


def test_evidence_rag_uses_all_presented_evidence_and_argo_selected_typology() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:ranked-{index}",
            origin="local_rag",
            evidence_level="abstract",
            scope="common",
            title=f"Étude classée {index}",
            authors=[f"Auteur {index}"],
            evidence_grade="A",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:ranked-{index}:abstract",
                    text=(
                        "L'étude décrit un résultat pertinent dans la matrice examinée et replace "
                        "l'observation dans les conditions expérimentales réellement appliquées. "
                        "Les auteurs détaillent la méthode de suivi, la temporalité des "
                        "prélèvements et les caractéristiques du lot étudié. Ils comparent les "
                        "observations entre "
                        "les conditions disponibles et rapportent les variations constatées sans "
                        "les étendre à une autre étape du procédé. La discussion distingue ce qui "
                        "est directement observé de ce qui relève de l'interprétation. Elle "
                        "précise "
                        "aussi les limites de la comparaison, la portée propre à la matrice et les "
                        "informations qui resteraient nécessaires pour généraliser le constat. "
                        "Ces éléments permettent de relier le résultat à son contexte, de le "
                        "rapprocher des travaux compatibles et de préserver les différences de "
                        "protocole lors de la synthèse."
                    ),
                )
            ],
        )
        for index in range(10)
    ]
    expected_ids = [f"common:ranked-{index}:abstract" for index in range(10)]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, *, json_schema, **_options):
            self.calls += 1
            assert json_schema["properties"]["response_format"]["enum"] == [
                "prose",
                "thematic_sections",
                "comparison",
                "process",
                "bullet_list",
            ]
            payload = json.loads(messages[1]["content"])
            assert [item["evidence_id"] for item in payload["evidence"]] == expected_ids
            assert "definition est facultatif" in messages[0]["content"]
            assert "Choisis toi-même la typologie" in messages[0]["content"]
            assert "trois à six phrases liées" in messages[0]["content"]
            if self.calls == 1:
                statements = [
                    {
                        "statement": (
                            "Les travaux retenus décrivent plusieurs résultats pertinents."
                        ),
                        "evidence_ids": [expected_ids[0]],
                        "section": "synthetic_answer",
                        "mechanism": None,
                    }
                ]
            else:
                assert "omitted relevant evidence elements" in messages[-1]["content"]
                assert expected_ids[-1] in messages[-1]["content"]
                sentence = (
                    "Les observations décrivent des résultats distincts dans leur contexte "
                    "expérimental, précisent les conditions étudiées, rapprochent les constats "
                    "compatibles, séparent les différences de protocole et conservent les limites "
                    "d'interprétation propres à chaque travail scientifique disponible. "
                )
                statements = [
                    {
                        "statement": sentence * 4,
                        "evidence_ids": expected_ids[0:2],
                        "section": "synthetic_answer",
                        "mechanism": None,
                    },
                    {
                        "statement": sentence * 4,
                        "evidence_ids": expected_ids[2:4],
                        "section": "documented_effect",
                        "mechanism": "Conditions expérimentales",
                    },
                    {
                        "statement": sentence * 4,
                        "evidence_ids": expected_ids[4:6],
                        "section": "documented_effect",
                        "mechanism": "Résultats convergents",
                    },
                    {
                        "statement": sentence * 4,
                        "evidence_ids": expected_ids[6:8],
                        "section": "documented_effect",
                        "mechanism": "Limites de transposition",
                    },
                    {
                        "statement": sentence * 4,
                        "evidence_ids": expected_ids[8:10],
                        "section": "documented_effect",
                        "mechanism": "Résultats complémentaires",
                    },
                ]
            return _response(
                json.dumps(
                    {
                        "status": "answerable",
                        "response_format": "thematic_sections",
                        "definition": (
                            "Cette synthèse situe les résultats dans leurs contextes "
                            "expérimentaux. "
                            "Elle distingue les constats, leurs conditions et leurs limites."
                        ),
                        "statements": statements,
                        "limitations": [],
                        "insufficiency_message": None,
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer(
        "Que montrent ces études sur le sujet ?",
        records,
    )

    assert client.calls == 2
    assert result.answer.response_format is ResponseStyle.THEMATIC_SECTIONS
    assert result.cited_evidence_ids == expected_ids
    assert result.source_record_ids == [f"common:ranked-{index}" for index in range(10)]
    assert result.answer_markdown.startswith("Cette synthèse situe les résultats")
    assert "## Conditions expérimentales" in result.answer_markdown
    assert "Étude classée 8" in result.answer_markdown
    assert result.generation_traces[0].presented_evidence_count == 10
    assert result.generation_traces[0].cited_evidence_count == 10


def test_evidence_rag_requires_one_representative_passage_per_article() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:article-{index}",
            origin="local_rag",
            evidence_level="full_text",
            scope="common",
            article_id=f"article-{index}",
            title=f"Article {index}",
            evidence_grade="A",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:article-{index}:chunk:{passage}",
                    chunk_id=passage,
                    page_start=passage,
                    page_end=passage,
                    text=(
                        f"L'article {index} documente un résultat pertinent dans la matrice "
                        "et décrit précisément les conditions expérimentales étudiées. "
                    )
                    * 6,
                )
                for passage in (1, 2)
            ],
        )
        for index in (1, 2)
    ]
    required_ids = [f"common:article-{index}:chunk:1" for index in (1, 2)]

    class FakeClient:
        def chat(self, messages, **_options):
            payload = json.loads(messages[1]["content"])
            assert len(payload["evidence"]) == 4
            assert payload["required_evidence_ids"] == required_ids
            answer = {
                "status": "answerable",
                "response_format": "prose",
                "definition": (
                    "Les deux articles examinent des observations documentées dans la matrice. "
                    "La synthèse rapproche leurs conditions expérimentales."
                ),
                "statements": [
                    {
                        "statement": (
                            "Les deux articles décrivent des résultats pertinents dans la matrice "
                            "et précisent les conditions expérimentales propres à leurs essais."
                        ),
                        "evidence_ids": required_ids,
                        "section": "synthetic_answer",
                        "mechanism": None,
                    }
                ],
                "limitations": [],
                "insufficiency_message": None,
            }
            return _response(f"<think>validated</think>\n```json\n{json.dumps(answer)}\n```")

    result = CiderEvidenceRagService(FakeClient(), answer_effort=AnswerEffort.CONCISE).answer(
        "Que montrent les deux articles dans cette matrice ?",
        records,
    )

    assert result.generation_status == "generated"
    assert result.generation_traces[0].presented_evidence_count == 2
    assert result.generation_traces[0].cited_evidence_count == 2


def test_deep_evidence_rag_reserves_output_room_after_hidden_reasoning() -> None:
    passage = ChatEvidencePassage(
        evidence_id="common:deep-budget:abstract",
        text=(
            "Les levures non-Saccharomyces contribuent à la formation de composés "
            "aromatiques dans la matrice fermentée et l'étude décrit les conditions "
            "expérimentales de cette observation."
        ),
    )
    record = ChatEvidenceRecord(
        record_id="common:deep-budget",
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title="Non-Saccharomyces aroma formation",
        evidence_grade="A",
        passages=[passage],
    )

    class FakeClient:
        def chat(self, _messages, *, max_output_tokens, **_options):
            assert max_output_tokens == 8_192
            return _response(
                json.dumps(
                    {
                        "status": "answerable",
                        "response_format": "prose",
                        "definition": (
                            "La synthèse porte sur la contribution aromatique documentée "
                            "pendant la fermentation."
                        ),
                        "statements": [
                            {
                                "statement": (
                                    "L'étude relie les levures non-Saccharomyces à la formation "
                                    "de composés aromatiques dans les conditions fermentaires "
                                    "qu'elle examine."
                                ),
                                "evidence_ids": [passage.evidence_id],
                                "section": "synthetic_answer",
                                "mechanism": None,
                            }
                        ],
                        "limitations": [],
                        "insufficiency_message": None,
                    },
                    ensure_ascii=False,
                )
            )

    result = CiderEvidenceRagService(
        FakeClient(),
        answer_effort=AnswerEffort.DEEP,
    ).answer("Quel rôle aromatique ces levures jouent-elles ?", [record])

    assert result.generation_status == "generated"
    assert result.cited_evidence_ids == [passage.evidence_id]


def test_evidence_rag_retries_an_ambiguous_wrapped_response() -> None:
    passage = ChatEvidencePassage(
        evidence_id="common:ambiguous-output:abstract",
        text="L'essai documente une contribution aromatique pendant la fermentation.",
    )
    record = ChatEvidenceRecord(
        record_id="common:ambiguous-output",
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title="Contribution aromatique fermentaire",
        evidence_grade="A",
        passages=[passage],
    )
    answer = {
        "status": "answerable",
        "response_format": "prose",
        "definition": "La synthèse concerne la contribution aromatique fermentaire.",
        "statements": [
            {
                "statement": (
                    "L'essai relie la fermentation étudiée à une contribution aromatique "
                    "documentée dans ses conditions expérimentales."
                ),
                "evidence_ids": [passage.evidence_id],
                "section": "synthetic_answer",
                "mechanism": None,
            }
        ],
        "limitations": [],
        "insufficiency_message": None,
    }

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, **_options):
            self.calls += 1
            if self.calls == 1:
                alternative = answer | {"definition": "Un second objet concurrent."}
                return _response(f"{json.dumps(answer)}\n{json.dumps(alternative)}")
            assert "invalid evidence RAG answer" in messages[-1]["content"]
            return _response(json.dumps(answer))

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer(
        "Quel rôle aromatique est documenté ?",
        [record],
    )

    assert client.calls == 2
    assert result.generation_status == "generated"


def test_evidence_rag_regenerates_telegraphic_paragraph_from_rich_evidence() -> None:
    evidence_text = (
        "Les auteurs décrivent les observations dans la matrice étudiée et précisent le cadre "
        "expérimental retenu. La méthode distingue les lots examinés, les moments de suivi et les "
        "conditions appliquées pendant l'essai. Les résultats sont présentés avec les variations "
        "observées entre les situations comparées. La discussion sépare les constats directement "
        "documentés des interprétations proposées. Elle précise la portée du travail, les limites "
        "liées au protocole et les informations manquantes pour une transposition à une autre "
        "matrice. Les auteurs rapprochent enfin les observations compatibles tout en conservant "
        "les différences de méthode et de temporalité. "
    ) * 2
    record = ChatEvidenceRecord(
        record_id="common:rich-study",
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title="Étude détaillée",
        authors=["Auteur Test"],
        evidence_grade="A",
        passages=[
            ChatEvidencePassage(
                evidence_id="common:rich-study:abstract",
                text=evidence_text,
            )
        ],
    )
    developed_paragraph = (
        "L'étude replace d'abord les observations dans la matrice et dans le cadre expérimental "
        "effectivement examinés. Elle précise que la lecture des résultats dépend des lots, des "
        "moments de suivi et des conditions appliquées pendant l'essai. Les variations sont "
        "présentées entre les situations comparées, en séparant les constats documentés des "
        "interprétations proposées. Le rapprochement avec les observations compatibles conserve "
        "les différences de méthode et de temporalité. La portée reste donc attachée au protocole "
        "décrit et à la matrice étudiée, tandis qu'une transposition demanderait les informations "
        "supplémentaires signalées dans la discussion."
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, **_options):
            self.calls += 1
            if self.calls == 1:
                assert "paragraphe scientifique substantiel" in messages[0]["content"]
                statement = "Les observations dépendent du contexte expérimental étudié."
            else:
                assert (
                    "paragraph that is too short for its cited evidence" in messages[-1]["content"]
                )
                assert "n'ajoute ni remplissage ni connaissance externe" in messages[-1]["content"]
                statement = developed_paragraph
            return _response(
                json.dumps(
                    {
                        "status": "answerable",
                        "response_format": "prose",
                        "definition": (
                            "Cette synthèse situe les observations dans la matrice étudiée. "
                            "Elle examine leur contexte, leur portée et leurs limites."
                        ),
                        "statements": [
                            {
                                "statement": statement,
                                "evidence_ids": ["common:rich-study:abstract"],
                                "section": "synthetic_answer",
                                "mechanism": None,
                            }
                        ],
                        "limitations": [],
                        "insufficiency_message": None,
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer(
        "Que montrent les observations ?",
        [record],
    )

    assert client.calls == 2
    assert result.answer.statements[0].statement == developed_paragraph
    assert "(Test, n.d.)" in result.answer_markdown


def test_evidence_grounding_requires_more_global_text_for_many_rich_citations() -> None:
    evidence: dict[str, tuple[ChatEvidenceRecord, ChatEvidencePassage]] = {}
    evidence_ids: list[str] = []
    source_text = (
        "Le travail situe les observations dans la matrice étudiée, décrit les conditions de "
        "comparaison et distingue les constats des interprétations. La méthode précise le suivi, "
        "la temporalité et les différences de protocole. La discussion délimite la portée des "
        "résultats et les informations manquantes pour les transposer. "
    ) * 4
    for index in range(8):
        evidence_id = f"common:rich-{index}:abstract"
        passage = ChatEvidencePassage(evidence_id=evidence_id, text=source_text)
        record = ChatEvidenceRecord(
            record_id=f"common:rich-{index}",
            origin="local_rag",
            evidence_level="abstract",
            scope="common",
            title=f"Étude riche {index}",
            evidence_grade="A",
            passages=[passage],
        )
        evidence_ids.append(evidence_id)
        evidence[evidence_id] = (record, passage)

    paragraph = (
        "Les travaux replacent les observations dans leur matrice, décrivent les conditions "
        "comparées, distinguent les constats des interprétations et précisent la portée ainsi que "
        "les limites documentées. "
    )
    answer = CiderEvidenceAnswer(
        status="answerable",
        response_format="prose",
        definition=(
            "Les observations concernent des cadres expérimentaux distincts. "
            "Elle rapproche les résultats tout en conservant leurs limites."
        ),
        statements=[
            CitedEvidenceStatement(
                statement=paragraph * 3,
                evidence_ids=evidence_ids[index : index + 2],
            )
            for index in range(0, 8, 2)
        ],
        limitations=[],
    )

    with pytest.raises(
        RuntimeError,
        match="synthesis is too short for the selected evidence and requested effort",
    ):
        _validate_evidence_grounding(
            answer,
            evidence,
            set(evidence_ids),
            None,
            require_structured_response=True,
            require_contextual_introduction=True,
            question="Que montrent ces études ?",
            required_evidence_ids=frozenset(evidence_ids),
            answer_effort=AnswerEffort.BALANCED,
        )

    developed = answer.model_copy(deep=True)
    developed.statements = [
        statement.model_copy(update={"statement": paragraph * 5}) for statement in answer.statements
    ]
    used_ids = _validate_evidence_grounding(
        developed,
        evidence,
        set(evidence_ids),
        None,
        require_structured_response=True,
        require_contextual_introduction=True,
        question="Que montrent ces études ?",
        required_evidence_ids=frozenset(evidence_ids),
        answer_effort=AnswerEffort.BALANCED,
    )

    assert used_ids == evidence_ids


def test_evidence_grounding_rejects_a_sentence_truncated_after_scientific_initial() -> None:
    evidence_id = "common:yeast:abstract"
    passage = ChatEvidencePassage(
        evidence_id=evidence_id,
        text="Hanseniaspora vineae was compared with Saccharomyces cerevisiae in cider.",
    )
    record = ChatEvidenceRecord(
        record_id="common:yeast",
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title="Yeast comparison",
        evidence_grade="A",
        passages=[passage],
    )
    answer = CiderEvidenceAnswer(
        status="answerable",
        response_format="prose",
        statements=[
            CitedEvidenceStatement(
                statement="Hanseniaspora vineae produit des esters absents avec S.",
                evidence_ids=[evidence_id],
            )
        ],
        limitations=[],
    )

    with pytest.raises(RuntimeError) as exc_info:
        _validate_evidence_grounding(
            answer,
            {evidence_id: (record, passage)},
            {evidence_id},
            None,
        )

    assert ScientificValidationReason.INVALID_PROSE_STRUCTURE in exc_info.value.reasons

    correction = _validation_correction_message(
        exc_info.value,
        output_language_label="français",
    )

    assert "ne termine jamais une phrase par une initiale isolée" in correction

    connector_answer = answer.model_copy(deep=True)
    connector_answer.statements[0] = connector_answer.statements[0].model_copy(
        update={
            "statement": "De plus, Hanseniaspora vineae a été comparée à Saccharomyces cerevisiae."
        }
    )
    with pytest.raises(RuntimeError) as connector_error:
        _validate_evidence_grounding(
            connector_answer,
            {evidence_id: (record, passage)},
            {evidence_id},
            None,
        )

    assert ScientificValidationReason.INVALID_PROSE_STRUCTURE in connector_error.value.reasons

    contextless_answer = answer.model_copy(deep=True)
    contextless_answer.statements[0] = contextless_answer.statements[0].model_copy(
        update={"statement": "Parmi elles figurent Douce Coët Ligné et Doux Normandie."}
    )
    with pytest.raises(RuntimeError) as contextless_error:
        _validate_evidence_grounding(
            contextless_answer,
            {evidence_id: (record, passage)},
            {evidence_id},
            None,
        )

    assert ScientificValidationReason.INVALID_PROSE_STRUCTURE in contextless_error.value.reasons

    orphan_answer = answer.model_copy(deep=True)
    orphan_answer.statements[0] = orphan_answer.statements[0].model_copy(
        update={"statement": "marxianus atteint la concentration la plus élevée."}
    )
    with pytest.raises(RuntimeError) as orphan_error:
        _validate_evidence_grounding(
            orphan_answer,
            {evidence_id: (record, passage)},
            {evidence_id},
            None,
        )

    assert ScientificValidationReason.INVALID_PROSE_STRUCTURE in orphan_error.value.reasons

    duplicate_answer = answer.model_copy(deep=True)
    duplicate_answer.statements = [
        CitedEvidenceStatement(
            statement=(
                "Hanseniaspora vineae a été comparée à Saccharomyces cerevisiae dans des "
                "essais de fermentation du cidre."
            ),
            evidence_ids=[evidence_id],
        ),
        CitedEvidenceStatement(
            statement=(
                "Dans les essais de fermentation du cidre, Hanseniaspora vineae a été comparée "
                "à Saccharomyces cerevisiae."
            ),
            evidence_ids=[evidence_id],
        ),
    ]
    with pytest.raises(RuntimeError) as duplicate_error:
        _validate_evidence_grounding(
            duplicate_answer,
            {evidence_id: (record, passage)},
            {evidence_id},
            None,
        )

    assert ScientificValidationReason.INVALID_PROSE_STRUCTURE in duplicate_error.value.reasons


def test_evidence_grounding_reports_all_safe_validation_failures_together() -> None:
    source_text = (
        "Le travail décrit la matrice, les conditions expérimentales et les observations "
        "qualitatives obtenues pendant le suivi. Il compare les lots, précise la temporalité, "
        "distingue les résultats des interprétations et discute les limites du protocole sans "
        "rapporter de valeur numérique. "
    ) * 3
    evidence: dict[str, tuple[ChatEvidenceRecord, ChatEvidencePassage]] = {}
    evidence_ids: list[str] = []
    for index in range(2):
        evidence_id = f"common:cumulative-{index}:abstract"
        passage = ChatEvidencePassage(evidence_id=evidence_id, text=source_text)
        record = ChatEvidenceRecord(
            record_id=f"common:cumulative-{index}",
            origin="local_rag",
            evidence_level="abstract",
            scope="common",
            title=f"Étude cumulative {index}",
            evidence_grade="A",
            passages=[passage],
        )
        evidence_ids.append(evidence_id)
        evidence[evidence_id] = (record, passage)

    answer = CiderEvidenceAnswer(
        response_format="prose",
        definition=(
            "Cette synthèse examine les observations rapportées dans les deux études. "
            "Elle les replace dans leur matrice et leurs conditions expérimentales."
        ),
        statements=[
            CitedEvidenceStatement(
                statement="Le RAG indique une hausse de 42 %.",
                evidence_ids=[evidence_ids[0]],
            )
        ],
        limitations=[],
    )

    with pytest.raises(RuntimeError) as exc_info:
        _validate_evidence_grounding(
            answer,
            evidence,
            set(evidence_ids),
            ResponseStyle.PROSE,
            require_structured_response=True,
            require_contextual_introduction=True,
            question="Que montrent ces études ?",
            required_evidence_ids=frozenset(evidence_ids),
            answer_effort=AnswerEffort.BALANCED,
        )

    assert set(exc_info.value.reasons) >= {
        ScientificValidationReason.MISSING_REQUIRED_EVIDENCE,
        ScientificValidationReason.INTERNAL_PROCESS_LEAK,
        ScientificValidationReason.PARAGRAPH_TOO_SHORT,
        ScientificValidationReason.UNSUPPORTED_NUMERIC_CLAIM,
    }


def test_final_prompt_budget_failure_returns_validated_cited_drafts() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"record-{index}",
            origin="local_rag",
            evidence_level="full_text",
            scope="common",
            article_id=f"article-{index}",
            title=f"Study {index}",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"record-{index}:chunk:1",
                    chunk_id=1,
                    page_start=index,
                    page_end=index,
                    text="The study documents a measured effect.",
                )
            ],
        )
        for index in (1, 2)
    ]

    class FinalBudgetFailureService(CiderEvidenceRagService):
        def _fit_prompt_payload(self, system, payload, **options):
            if payload.get("facet_drafts"):
                raise _PromptBudgetError("irreducible final prompt overhead")
            return super()._fit_prompt_payload(system, payload, **options)

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": "The study documents a measured effect.",
                                "evidence_ids": [f"record-{self.calls}:chunk:1"],
                            }
                        ],
                        "limitations": [],
                    }
                )
            )

    client = FakeClient()
    result = FinalBudgetFailureService(client).answer_faceted(
        "Compare the documented effects and conditions.",
        records,
        facets=[
            ScientificFacet(
                key="effects", label="Effects", terms_fr=["effets"], terms_en=["effects"]
            ),
            ScientificFacet(
                key="conditions",
                label="Conditions",
                terms_fr=["conditions"],
                terms_en=["conditions"],
            ),
        ],
    )

    assert client.calls == 2
    assert result.generation_status == "partial_generated"
    assert result.cited_evidence_ids == ["record-1:chunk:1", "record-2:chunk:1"]
    assert result.generation_traces[-1].phase == "final_assembly"
    assert result.generation_traces[-1].outcome == "failed"
    assert result.generation_traces[-1].request_count == 0


def test_late_quota_returns_validated_cited_drafts_without_restarting() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"record-{index}",
            origin="local_rag",
            evidence_level="full_text",
            scope="common",
            article_id=f"article-{index}",
            title=f"Study {index}",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"record-{index}:chunk:1",
                    chunk_id=1,
                    page_start=index,
                    page_end=index,
                    text="The study documents a measured effect.",
                )
            ],
        )
        for index in (1, 2)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            if self.calls == 3:
                raise ArgoQuotaError("provider quota reached during final assembly")
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": "The study documents a measured effect.",
                                "evidence_ids": [f"record-{self.calls}:chunk:1"],
                            }
                        ],
                        "limitations": [],
                    }
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer_faceted(
        "Compare the documented effects and conditions.",
        records,
        facets=[
            ScientificFacet(
                key="effects", label="Effects", terms_fr=["effets"], terms_en=["effects"]
            ),
            ScientificFacet(
                key="conditions",
                label="Conditions",
                terms_fr=["conditions"],
                terms_en=["conditions"],
            ),
        ],
    )

    assert client.calls == 3
    assert result.generation_status == "partial_generated"
    assert result.cited_evidence_ids == ["record-1:chunk:1", "record-2:chunk:1"]
    assert result.generation_traces[-1].phase == "final_assembly"
    assert result.generation_traces[-1].outcome == "failed"


@pytest.mark.parametrize(
    ("authors", "expected"),
    [
        (["Ada Test"], "Test, A."),
        (["Ada Test", "Bob Doe"], "Test, A., & Doe, B."),
        (
            ["Ada Test", "Bob Doe", "Chloé Roe"],
            "Test, A., Doe, B., & Roe, C.",
        ),
    ],
)
def test_apa_reference_formats_author_variants(authors: list[str], expected: str) -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")
    record.authors = authors

    assert _apa_reference(record).startswith(f"{expected} (2025).")


def test_apa_reference_does_not_invent_a_missing_author() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")
    record.authors = []

    reference = _apa_reference(record)

    assert reference.startswith("Cider microbiology. (2025).")
    assert "Anonymous" not in reference


def test_apa_reference_does_not_invent_a_missing_doi() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")
    record.doi = None
    record.url = None

    reference = _apa_reference(record)

    assert "doi.org" not in reference
    assert reference.endswith("*Cider Science*.")


def test_bibliographic_cleanup_deduplicates_authors_and_rejects_corrupt_metadata() -> None:
    assert _clean_author_names(["Ada Test", " Ada  Test ", "B, Z."]) == ["Ada Test"]
    assert _renderable_doi("https://doi.org/10.1000/Valid") == "10.1000/valid"
    assert _renderable_doi("not-a-doi") is None

    record = _record("11111111-1111-1111-1111-111111111111", "not-a-doi")
    record.authors = ["B, Z."]

    reference = _apa_reference(record)

    assert "B, Z." not in reference
    assert "doi.org" not in reference
    assert "Métadonnées bibliographiques incomplètes" in reference


def test_evidence_rag_uses_only_indirect_evidence_with_explicit_scope() -> None:
    passage = ChatEvidencePassage(
        evidence_id="common:indirect:abstract",
        text="The study concerns a related downstream process.",
    )
    record = ChatEvidenceRecord(
        record_id="common:indirect",
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title="Related downstream process",
        evidence_grade="B",
        passages=[passage],
    )

    class FakeClient:
        def chat(self, _messages, **_options):
            return GenerationResponse(
                content=json.dumps(
                    {
                        "status": "answerable",
                        "response_format": "prose",
                        "definition": (
                            "La question porte sur l'effet du procédé exact demandé. "
                            "La synthèse distingue explicitement les résultats issus du "
                            "procédé aval."
                        ),
                        "statements": [
                            {
                                "statement": (
                                    "Preuve indirecte : cette étude porte sur un procédé aval "
                                    "connexe et non sur le procédé exact demandé."
                                ),
                                "evidence_ids": ["common:indirect:abstract"],
                                "section": "synthetic_answer",
                                "mechanism": None,
                            }
                        ],
                        "limitations": ["La transposition au procédé exact reste indirecte."],
                        "insufficiency_message": None,
                    }
                ),
                model="test",
                done_reason="stop",
                metrics=GenerationMetrics(
                    total_duration_seconds=0.1,
                    load_duration_seconds=0.0,
                    prompt_eval_count=1,
                    prompt_eval_duration_seconds=0.0,
                    eval_count=1,
                    eval_duration_seconds=0.1,
                ),
            )

    result = CiderEvidenceRagService(FakeClient()).answer(
        "Quel est l'effet du procédé exact ?",
        [record],
    )

    assert result.answer.status == "answerable"
    assert result.cited_evidence_ids == ["common:indirect:abstract"]
    assert result.source_record_ids == ["common:indirect"]
    assert "Preuve indirecte" not in result.answer_markdown
    assert "Définition retenue" not in result.answer_markdown
    assert result.answer_markdown.startswith("La question porte sur l'effet du procédé exact")
    assert "## Limites des preuves" not in result.answer_markdown
    assert "Related downstream process" in result.answer_markdown
    assert "## Références" in result.answer_markdown


def test_evidence_rag_accepts_study_context_without_indirect_label() -> None:
    passage = ChatEvidencePassage(
        evidence_id="common:indirect-warning:abstract",
        text="The study describes a transferable mechanism in a related matrix.",
    )
    record = ChatEvidenceRecord(
        record_id="common:indirect-warning",
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title="Transferable mechanism",
        evidence_grade="B",
        passages=[passage],
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            return _response(
                json.dumps(
                    {
                        "status": "answerable",
                        "response_format": "prose",
                        "definition": None,
                        "statements": [
                            {
                                "statement": (
                                    "L'étude décrit un mécanisme transposable dans une matrice "
                                    "connexe."
                                ),
                                "evidence_ids": ["common:indirect-warning:abstract"],
                                "section": "synthetic_answer",
                                "mechanism": None,
                            }
                        ],
                        "limitations": [],
                        "insufficiency_message": None,
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer(
        "Que suggère ce mécanisme dans la matrice demandée ?",
        [record],
    )

    assert client.calls == 2
    assert result.generation_status == "generated"
    assert result.cited_evidence_ids == ["common:indirect-warning:abstract"]
    assert result.validation_warning_codes == []


def test_evidence_rag_never_returns_an_abstention_when_a_b_evidence_is_mandatory() -> None:
    passage = ChatEvidencePassage(
        evidence_id="common:adjacent:abstract",
        text=(
            "The study describes filtration performance but does not compare the two "
            "clarification agents asked about."
        ),
    )
    record = ChatEvidenceRecord(
        record_id="common:adjacent",
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title="Adjacent clarification process",
        evidence_grade="A",
        passages=[passage],
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, *, json_schema, max_output_tokens, temperature=None):
            self.calls += 1
            assert temperature == (None if self.calls == 1 else 0.1)
            assert max_output_tokens == 4096
            assert json_schema["properties"]["status"]["enum"] == [
                "answerable",
                "insufficient",
            ]
            assert json_schema["properties"]["statements"]["minItems"] == 0
            assert "status=insufficient" in messages[0]["content"]
            if self.calls == 1:
                return _response(
                    json.dumps(
                        {
                            "status": "answerable",
                            "response_format": "prose",
                            "definition": "Comparaison de deux agents de clarification.",
                            "statements": [],
                            "limitations": [],
                            "insufficiency_message": None,
                        },
                        ensure_ascii=False,
                    )
                )
            if self.calls == 3:
                assert '"code": "unjustified_abstention"' in messages[-1]["content"]
                assert "utilise status=answerable" in messages[-1]["content"]
            return _response(
                json.dumps(
                    {
                        "status": "insufficient",
                        "response_format": "prose",
                        "definition": "Comparaison de deux agents de clarification.",
                        "statements": [],
                        "limitations": [
                            "Le document porte sur un procédé adjacent sans comparaison directe."
                        ],
                        "insufficiency_message": (
                            "Les preuves récupérées ne permettent pas de comparer directement "
                            "les deux agents demandés."
                        ),
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    with pytest.raises(ArgoScientificValidationError) as raised:
        CiderEvidenceRagService(client).answer(
            "Quel agent de clarification est le plus efficace ?",
            [record],
        )

    assert client.calls == 10
    assert set(raised.value.reasons) == {
        ScientificValidationReason.UNJUSTIFIED_ABSTENTION,
        ScientificValidationReason.MISSING_REQUIRED_EVIDENCE,
    }
    assert raised.value.generation_traces[0].model_dump() == {
        "schema_version": 1,
        "phase": "evidence",
        "outcome": "failed",
        "request_count": 10,
        "validation_retries": 9,
        "length_retries": 0,
        "correction_temperature": 0.1,
        "prompt_tokens": 500,
        "completion_tokens": 200,
        "validation_codes": [
            "empty_answerable_statements",
            "unjustified_abstention",
            "missing_required_evidence",
        ],
        "presented_evidence_count": 1,
        "cited_evidence_count": 0,
    }


def test_pilot_rag_constrains_ids_and_renders_abstract_citations() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")

    class FakeClient:
        def chat(self, messages, *, json_schema, max_output_tokens, **_options):
            assert max_output_tokens == 4096
            assert json_schema["properties"]["response_format"] == {
                "type": "string",
                "enum": ["prose", "bullet_list"],
            }
            assert "abstracts" in messages[1]["content"]
            assert json.loads(messages[1]["content"])["output_language"] == "fr"
            assert json.loads(messages[1]["content"])["conversation_history"] == [
                {"role": "user", "content": "Parlons des fermentations."}
            ]
            assert "langue du message utilisateur courant" in messages[0]["content"]
            assert "ton froid, factuel et non promotionnel" in messages[0]["content"]
            assert "phrases simples" in messages[0]["content"]
            assert "vocabulaire scientifique précis" in messages[0]["content"]
            assert "résultats positifs et négatifs pertinents" in messages[0]["content"]
            assert "faits des biais, erreurs et limites documentés" in messages[0]["content"]
            assert "amélioration non démontrée" in messages[0]["content"]
            assert "jamais comme un résultat acquis" in messages[0]["content"]
            assert "ni emoji, ni émoticône" in messages[0]["content"]
            assert "ni compliment, ni superlatif non étayé" in messages[0]["content"]
            assert "choisis response_format=prose ou" in messages[0]["content"]
            enum = json_schema["$defs"]["CitedAbstractStatement"]["properties"]["record_ids"][
                "items"
            ]["enum"]
            assert enum == [record.record_id]
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": "Les levures pilotent la fermentation.",
                                "record_ids": [record.record_id],
                            }
                        ],
                        "limitations": ["Un abstract ne remplace pas le texte intégral."],
                    },
                    ensure_ascii=False,
                )
            )

    result = CiderAbstractRagService(FakeClient()).answer(
        "Quels microorganismes surveiller ?",
        [record],
        conversation_history=[{"role": "user", "content": "Parlons des fermentations."}],
    )

    assert result.source_record_ids == [record.record_id]
    assert not result.answer_markdown.startswith("-")
    assert "(Test, 2025)" in result.answer_markdown
    assert "## Références" in result.answer_markdown
    assert "Test, A. (2025). Cider microbiology. *Cider Science*" in result.answer_markdown
    assert "https://doi.org/10.1000/cider" in result.answer_markdown
    assert "ne remplace pas le texte intégral" in result.answer_markdown
    assert result.prompt_tokens == 100


def test_evidence_rag_translates_every_generated_field_to_question_language() -> None:
    passage = ChatEvidencePassage(
        evidence_id="common:language:abstract",
        text="The study observed aroma changes during wood aging.",
    )
    record = ChatEvidenceRecord(
        record_id="common:language",
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title="Aroma changes during wood aging",
        evidence_grade="A",
        passages=[passage],
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, **options):
            self.calls += 1
            payload = json.loads(messages[1]["content"])
            assert payload["output_language"] == "fr"
            assert "traduis son contenu scientifique" in messages[0]["content"]
            if self.calls == 1:
                assert options.get("temperature") is None
                definition = "The study shows aging effects."
            else:
                assert options["temperature"] == 0.1
                assert "Traduis intégralement chaque champ rédactionnel" in messages[-1]["content"]
                definition = (
                    "Le vieillissement sous bois est le procédé étudié dans les preuves. "
                    "La synthèse examine les modifications aromatiques observées pendant "
                    "cette étape."
                )
            return _response(
                json.dumps(
                    {
                        "status": "answerable",
                        "response_format": "prose",
                        "definition": definition,
                        "statements": [
                            {
                                "statement": (
                                    "L'étude observe une modification des arômes pendant le "
                                    "vieillissement sous bois."
                                ),
                                "evidence_ids": ["common:language:abstract"],
                                "section": "synthetic_answer",
                                "mechanism": None,
                            }
                        ],
                        "limitations": [
                            "Les preuves disponibles reposent uniquement sur un abstract."
                        ],
                        "insufficiency_message": None,
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer(
        "Quels effets le vieillissement sous bois produit-il ?",
        [record],
    )

    assert client.calls == 2
    assert result.answer.definition is not None
    assert result.answer.definition.startswith("Le vieillissement sous bois est le procédé étudié")
    assert "modifications aromatiques" in result.answer.definition
    assert "The study shows" not in result.answer_markdown


def test_evidence_rag_uses_full_text_passages_and_renders_exact_pages() -> None:
    passage = ChatEvidencePassage(
        evidence_id="common:article-1:chunk:42",
        chunk_id=42,
        section="Results",
        page_start=4,
        page_end=5,
        text="Fermentation at 18 °C increased ester production in the cider trial.",
    )
    record = ChatEvidenceRecord(
        record_id="common:article-1",
        origin="local_rag",
        evidence_level="full_text",
        scope="common",
        article_id="article-1",
        title="Temperature and cider aroma",
        authors=["Ada Test"],
        doi="10.1000/full-text",
        journal="Cider Science",
        publication_year=2025,
        providers=["local"],
        url="https://doi.org/10.1000/full-text",
        passages=[passage],
    )

    class FakeClient:
        def chat(self, messages, *, json_schema, max_output_tokens, **_options):
            assert max_output_tokens == 4096
            assert "matrice ou le procédé exact" in messages[0]["content"]
            assert "Une condition expérimentale ne constitue jamais" in messages[0]["content"]
            payload = json.loads(messages[1]["content"])
            assert payload["query_interpretation"] == {
                "concept_definition": "Fermentation conduite à température contrôlée.",
                "ambiguities": ["température de fermentation ou de stockage"],
                "excluded_concepts": ["stockage après fermentation"],
            }
            assert payload["evidence"][0]["evidence_level"] == "full_text"
            assert payload["evidence"][0]["page_start"] == 4
            assert payload["evidence"][0]["text"].startswith("Fermentation at 18")
            assert payload["documentary_coverage_notes"] == [
                "Axe « mécanismes » : couverture documentaire partial."
            ]
            assert payload["organizational_reasoning"] == (
                "Cadre wiki : distinguer la mesure, le mécanisme et la décision."
            )
            assert (
                "contraintes de prudence, pas des preuves scientifiques" in (messages[0]["content"])
            )
            enum = json_schema["$defs"]["CitedEvidenceStatement"]["properties"]["evidence_ids"][
                "items"
            ]["enum"]
            assert enum == [passage.evidence_id]
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "definition": (
                            "L'article porte sur la température appliquée pendant la fermentation. "
                            "La synthèse précise le résultat aromatique observé et sa portée."
                        ),
                        "statements": [
                            {
                                "statement": (
                                    "À 18 °C, l'essai a observé une production accrue d'esters."
                                ),
                                "evidence_ids": [passage.evidence_id],
                            }
                        ],
                        "limitations": ["Les mécanismes moléculaires ne sont pas documentés ici."],
                    },
                    ensure_ascii=False,
                )
            )

    service = CiderEvidenceRagService(FakeClient())
    service.organizational_reasoning_context = (
        "Cadre wiki : distinguer la mesure, le mécanisme et la décision."
    )
    result = service.answer(
        "Que montre l'article sur la température ?",
        [record],
        coverage_notes=["Axe « mécanismes » : couverture documentaire partial."],
        concept_definition="Fermentation conduite à température contrôlée.",
        ambiguities=["température de fermentation ou de stockage"],
        excluded_concepts=["stockage après fermentation"],
    )

    assert result.source_record_ids == ["common:article-1"]
    assert result.cited_evidence_ids == [passage.evidence_id]
    assert "(Test, 2025, pp. 4–5)" in result.answer_markdown
    assert "abstract ne remplace" not in result.answer_markdown
    assert "mécanismes moléculaires" in result.answer_markdown


def test_evidence_rag_stops_after_reviewer_generation() -> None:
    passage = ChatEvidencePassage(
        evidence_id="common:article-1:chunk:42",
        chunk_id=42,
        section="Results",
        page_start=4,
        page_end=5,
        text="Fermentation increased ester production in the cider trial.",
    )
    record = ChatEvidenceRecord(
        record_id="common:article-1",
        origin="local_rag",
        evidence_level="full_text",
        scope="common",
        article_id="article-1",
        title="Temperature and cider aroma",
        authors=["Ada Test"],
        publication_year=2025,
        providers=["local"],
        passages=[passage],
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "definition": "La question porte sur l'effet observé pendant l'essai.",
                        "statements": [
                            {
                                "statement": "La production a augmente de 15 %.",
                                "evidence_ids": [passage.evidence_id],
                            }
                        ],
                        "limitations": [],
                    }
                )
            )

    client = FakeClient()
    with pytest.raises(ArgoScientificValidationError, match="numeric value 15"):
        CiderEvidenceRagService(client).answer("Quel est l'effet observe ?", [record])

    assert client.calls == 2


def test_evidence_rag_returns_best_safe_answer_with_quality_warning_after_ten_requests() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:safe-{index}",
            origin="local_rag",
            evidence_level="abstract",
            scope="common",
            title=f"Étude sûre {index}",
            evidence_grade="A",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:safe-{index}:abstract",
                    text=f"L'étude {index} décrit une observation distincte dans la matrice.",
                )
            ],
        )
        for index in range(1, 3)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, **_options):
            self.calls += 1
            if self.calls > 1:
                assert len(messages) == 3
                assert '"code": "missing_required_evidence"' in messages[-1]["content"]
                assert "Intègre chaque preuve A ou B" in messages[-1]["content"]
            return _response(
                json.dumps(
                    {
                        "status": "answerable",
                        "response_format": "prose",
                        "definition": (
                            "La question porte sur deux observations dans la matrice étudiée. "
                            "La synthèse examine leur portée documentaire respective."
                        ),
                        "statements": [
                            {
                                "statement": (
                                    "La première étude décrit une observation distincte dans la "
                                    "matrice."
                                ),
                                "evidence_ids": ["common:safe-1:abstract"],
                                "section": "synthetic_answer",
                                "mechanism": None,
                            }
                        ],
                        "limitations": [],
                        "insufficiency_message": None,
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer(
        "Que montrent ces deux études dans la matrice ?",
        records,
    )

    assert client.calls == 10
    assert result.generation_status == "partial_generated"
    assert result.validation_warning_codes == ["missing_required_evidence"]
    assert result.cited_evidence_ids == ["common:safe-1:abstract"]
    assert "certains passages pertinents retrouvés" in result.answer_markdown
    assert result.generation_traces[0].request_count == 10
    assert result.generation_traces[0].validation_retries == 9


def test_evidence_rag_prefers_evidence_coverage_over_fewer_style_warnings() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:coverage-{index}",
            origin="local_rag",
            evidence_level="abstract",
            scope="common",
            title=f"Étude de couverture {index}",
            evidence_grade="A",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:coverage-{index}:abstract",
                    text=f"L'étude {index} décrit une observation distincte dans la matrice.",
                )
            ],
        )
        for index in range(1, 3)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            complete = self.calls > 1
            return _response(
                json.dumps(
                    {
                        "status": "answerable",
                        "response_format": "prose",
                        "definition": (
                            "- Bref."
                            if complete
                            else (
                                "La question porte sur deux observations dans la matrice. "
                                "La synthèse examine leur portée documentaire."
                            )
                        ),
                        "statements": [
                            {
                                "statement": (
                                    "Les études décrivent des observations distinctes dans la "
                                    "matrice."
                                ),
                                "evidence_ids": (
                                    [
                                        "common:coverage-1:abstract",
                                        "common:coverage-2:abstract",
                                    ]
                                    if complete
                                    else ["common:coverage-1:abstract"]
                                ),
                                "section": "synthetic_answer",
                                "mechanism": None,
                            }
                        ],
                        "limitations": [],
                        "insufficiency_message": None,
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer(
        "Que montrent ces deux études dans la matrice ?",
        records,
    )

    assert client.calls == 2
    assert result.cited_evidence_ids == [
        "common:coverage-1:abstract",
        "common:coverage-2:abstract",
    ]
    assert result.validation_warning_codes == []


def test_evidence_rag_accumulates_safe_paragraphs_across_correction_attempts() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:cumulative-{index}",
            origin="local_rag",
            evidence_level="abstract",
            scope="common",
            title=f"Étude cumulative {index}",
            evidence_grade="A",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:cumulative-{index}:abstract",
                    text=f"L'étude {index} décrit une observation distincte dans la matrice.",
                )
            ],
        )
        for index in range(1, 3)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, **_options):
            self.calls += 1
            evidence_id = f"common:cumulative-{self.calls}:abstract"
            if self.calls == 2:
                assert "common:cumulative-2:abstract" in messages[-1]["content"]
            return _response(
                json.dumps(
                    {
                        "status": "answerable",
                        "response_format": "prose",
                        "definition": (
                            "Ces études décrivent deux observations dans la matrice considérée. "
                            "La synthèse en précise la portée documentaire."
                        ),
                        "statements": [
                            {
                                "statement": (
                                    f"L'étude {self.calls} décrit une observation distincte "
                                    "dans la matrice."
                                ),
                                "evidence_ids": [evidence_id],
                                "section": "synthetic_answer",
                                "mechanism": None,
                            }
                        ],
                        "limitations": [],
                        "insufficiency_message": None,
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer(
        "Que montrent ces deux études dans la matrice ?",
        records,
    )

    assert client.calls == 2
    assert result.generation_status == "generated"
    assert result.cited_evidence_ids == [
        "common:cumulative-1:abstract",
        "common:cumulative-2:abstract",
    ]
    assert len(result.answer.statements) == 2


def test_evidence_rag_keeps_grounded_paragraph_from_an_earlier_blocked_attempt() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:mixed-{index}",
            origin="local_rag",
            evidence_level="abstract",
            scope="common",
            title=f"Étude mixte {index}",
            evidence_grade="A",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:mixed-{index}:abstract",
                    text=f"L'étude {index} décrit une observation dans la matrice.",
                )
            ],
        )
        for index in range(1, 3)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            if self.calls == 1:
                payload = {
                    "status": "answerable",
                    "response_format": "prose",
                    "definition": "Le RAG a retrouvé deux études dans la matrice.",
                    "statements": [
                        {
                            "statement": (
                                "La première étude décrit une observation dans la matrice."
                            ),
                            "evidence_ids": ["common:mixed-1:abstract"],
                            "section": "synthetic_answer",
                            "mechanism": None,
                        },
                        {
                            "statement": "La seconde étude rapporte une hausse de 15 %.",
                            "evidence_ids": ["common:mixed-2:abstract"],
                            "section": "documented_effect",
                            "mechanism": "Effet quantifié",
                        },
                    ],
                    "limitations": [],
                    "insufficiency_message": None,
                }
            else:
                payload = {
                    "status": "insufficient",
                    "response_format": "prose",
                    "definition": "Les documents ne répondent pas directement à la question.",
                    "statements": [],
                    "limitations": [],
                    "insufficiency_message": (
                        "Aucune affirmation supplémentaire ne peut être établie."
                    ),
                }
            return _response(json.dumps(payload, ensure_ascii=False))

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer(
        "Que montrent ces deux études dans la matrice ?",
        records,
    )

    assert client.calls == 10
    assert result.generation_status == "partial_generated"
    assert result.cited_evidence_ids == ["common:mixed-1:abstract"]
    assert result.source_record_ids == ["common:mixed-1"]
    assert result.validation_warning_codes == ["missing_required_evidence"]
    assert "La première étude décrit" in result.answer_markdown
    assert "Le RAG" not in result.answer_markdown
    assert "15 %" not in result.answer_markdown


def test_evidence_rag_keeps_a_safe_candidate_when_a_retry_input_is_rejected() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:retry-{index}",
            origin="local_rag",
            evidence_level="abstract",
            scope="common",
            title=f"Étude {index}",
            evidence_grade="A",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:retry-{index}:abstract",
                    text=f"L'étude {index} décrit une observation dans la matrice.",
                )
            ],
        )
        for index in range(1, 3)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            if self.calls == 2:
                raise ValueError("LLM input exceeds the configured character limit")
            return _response(
                json.dumps(
                    {
                        "status": "answerable",
                        "response_format": "prose",
                        "definition": (
                            "La question porte sur deux observations dans une même matrice. "
                            "La synthèse examine leur portée documentaire."
                        ),
                        "statements": [
                            {
                                "statement": "La première étude décrit une observation.",
                                "evidence_ids": ["common:retry-1:abstract"],
                                "section": "synthetic_answer",
                                "mechanism": None,
                            }
                        ],
                        "limitations": [],
                        "insufficiency_message": None,
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer(
        "Que montrent les deux observations dans cette matrice ?",
        records,
    )

    assert client.calls == 2
    assert result.generation_status == "partial_generated"
    assert result.validation_warning_codes == ["missing_required_evidence"]
    assert result.cited_evidence_ids == ["common:retry-1:abstract"]


def test_evidence_rag_translates_retry_input_value_error_to_scientific_diagnostic() -> None:
    record = ChatEvidenceRecord(
        record_id="common:retry-hard",
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title="Étude",
        evidence_grade="A",
        passages=[
            ChatEvidencePassage(
                evidence_id="common:retry-hard:abstract",
                text="L'étude décrit une observation dans la matrice.",
            )
        ],
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            if self.calls == 2:
                raise ValueError("LLM input exceeds the configured character limit")
            return _response(
                json.dumps(
                    {
                        "status": "answerable",
                        "response_format": "prose",
                        "definition": (
                            "La question porte sur une observation dans la matrice. "
                            "La synthèse en examine la portée."
                        ),
                        "statements": [
                            {
                                "statement": "Une hausse de 15 % est observée.",
                                "evidence_ids": ["common:retry-hard:abstract"],
                                "section": "synthetic_answer",
                                "mechanism": None,
                            }
                        ],
                        "limitations": [],
                        "insufficiency_message": None,
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    with pytest.raises(ArgoScientificValidationError) as raised:
        CiderEvidenceRagService(client).answer(
            "Que montre cette observation dans la matrice ?",
            [record],
        )

    assert client.calls == 2
    assert raised.value.reason is ScientificValidationReason.PROMPT_BUDGET_EXCEEDED
    assert raised.value.prompt_tokens == 50
    assert raised.value.completion_tokens == 20
    assert raised.value.generation_traces[0].request_count == 2


def test_evidence_rag_correction_lists_every_current_violation_and_action() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:multi-{index}",
            origin="local_rag",
            evidence_level="abstract",
            scope="common",
            title=f"Étude {index}",
            evidence_grade="A",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:multi-{index}:abstract",
                    text=f"L'étude {index} décrit une observation dans la matrice.",
                )
            ],
        )
        for index in range(1, 3)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, **_options):
            self.calls += 1
            if self.calls == 2:
                correction = messages[-1]["content"]
                assert '"code": "missing_required_evidence"' in correction
                assert '"code": "missing_contextual_introduction"' not in correction
                assert '"code": "unsupported_numeric_claim"' in correction
                assert "scientific_blocker" in correction
                assert "quality_warning" in correction
            evidence_ids = (
                ["common:multi-1:abstract"]
                if self.calls == 1
                else ["common:multi-1:abstract", "common:multi-2:abstract"]
            )
            statement = (
                "Une hausse de 15 % est observée."
                if self.calls == 1
                else "Les deux études décrivent des observations dans la matrice étudiée."
            )
            return _response(
                json.dumps(
                    {
                        "status": "answerable",
                        "response_format": "prose",
                        "definition": (
                            "Bref."
                            if self.calls == 1
                            else (
                                "Les observations portent sur une même matrice. "
                                "La synthèse rapproche les deux études disponibles."
                            )
                        ),
                        "statements": [
                            {
                                "statement": statement,
                                "evidence_ids": evidence_ids,
                                "section": "synthetic_answer",
                                "mechanism": None,
                            }
                        ],
                        "limitations": [],
                        "insufficiency_message": None,
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer(
        "Que montrent les observations dans cette matrice ?",
        records,
    )

    assert client.calls == 2
    assert result.generation_status == "generated"
    assert result.cited_evidence_ids == [
        "common:multi-1:abstract",
        "common:multi-2:abstract",
    ]


def test_faceted_evidence_rag_keeps_cited_drafts_and_assembles_them() -> None:
    records = []
    for index, text in enumerate(
        [
            "Apple brandy oak ageing increased esters and oak lactones.",
            "Cider brandy wood ageing changed phenolic compounds and colour.",
            "Apple spirit maturation changed volatile compounds over time.",
        ],
        start=1,
    ):
        passage = ChatEvidencePassage(
            evidence_id=f"common:article-{index}:chunk:1",
            chunk_id=1,
            section="Results",
            page_start=index,
            page_end=index,
            text=text,
        )
        records.append(
            ChatEvidenceRecord(
                record_id=f"common:article-{index}",
                origin="local_rag",
                evidence_level="full_text",
                scope="common",
                article_id=f"article-{index}",
                title=f"Apple brandy study {index}",
                authors=["Ada Test"],
                publication_year=2025,
                providers=["local"],
                passages=[passage],
            )
        )

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, *, json_schema, max_output_tokens):
            self.calls += 1
            payload = json.loads(messages[1]["content"])
            enum = json_schema["$defs"]["CitedEvidenceStatement"]["properties"]["evidence_ids"][
                "items"
            ]["enum"]
            if self.calls <= 3:
                assert enum == [f"common:article-{self.calls}:chunk:1"]
                assert max_output_tokens == 3072
                assert json_schema["properties"]["statements"]["maxItems"] == 4
                assert json_schema["properties"]["response_format"] == {
                    "type": "string",
                    "const": "prose",
                }
                assert "facet_drafts" not in payload
                cited = enum[0]
            else:
                assert enum == [
                    "common:article-1:chunk:1",
                    "common:article-2:chunk:1",
                    "common:article-3:chunk:1",
                ]
                assert max_output_tokens == 4096
                assert json_schema["properties"]["statements"]["maxItems"] == 16
                assert json_schema["properties"]["response_format"]["enum"] == [
                    "prose",
                    "thematic_sections",
                    "comparison",
                    "process",
                    "bullet_list",
                ]
                statement_schema = json_schema["$defs"]["CitedEvidenceStatement"]
                assert "facet_key" in statement_schema["required"]
                assert statement_schema["properties"]["facet_key"]["enum"] == [
                    "aroma",
                    "evolution",
                    "structure",
                ]
                assert len(payload["facet_drafts"]) == 3
                assert "A=direct, B=indirect" in messages[0]["content"]
                assert "N'utilise jamais C ou D comme preuve" in messages[0]["content"]
                assert "status=insufficient" in messages[0]["content"]
                assert "status=answerable avec statements vide" in messages[0]["content"]
                cited = enum[0]
            statements = (
                [
                    {
                        "statement": "L'étude observe un effet documenté.",
                        "evidence_ids": [f"common:article-{index}:chunk:1"],
                        "facet_key": key,
                    }
                    for index, key in enumerate(["aroma", "structure", "evolution"], start=1)
                ]
                if self.calls > 3
                else [{"statement": "L'étude observe un effet documenté.", "evidence_ids": [cited]}]
            )
            return _response(
                json.dumps(
                    {
                        "response_format": ("thematic_sections" if self.calls > 3 else "prose"),
                        "definition": (
                            "La synthèse examine les effets documentés de l'élevage en barrique. "
                            "Elle distingue les dimensions aromatiques, structurelles et "
                            "temporelles."
                        ),
                        "statements": statements,
                        "limitations": [],
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer_faceted(
        "Quel est l'impact de l'élevage en barrique sur les arômes et la structure du Calvados ?",
        records,
        axis_candidate_ids={
            "aroma": ["common:article-1"],
            "structure": ["common:article-2"],
            "evolution": ["common:article-3"],
        },
    )

    assert client.calls == 4
    assert result.answer.response_format is ResponseStyle.THEMATIC_SECTIONS
    assert [draft.key for draft in result.facet_drafts] == ["aroma", "structure", "evolution"]
    assert result.facet_drafts[1].cited_evidence_ids == ["common:article-2:chunk:1"]
    assert result.prompt_tokens == 200
    assert result.completion_tokens == 80
    assert [trace.phase for trace in result.generation_traces] == [
        "facet_draft",
        "facet_draft",
        "facet_draft",
        "final_assembly",
    ]
    assert [trace.request_count for trace in result.generation_traces] == [1, 1, 1, 1]
    assert all(trace.correction_temperature is None for trace in result.generation_traces)


def test_faceted_final_assembly_failure_returns_cited_partial_drafts() -> None:
    records = []
    for index in range(1, 4):
        passage = ChatEvidencePassage(
            evidence_id=f"common:article-{index}:chunk:1",
            text=f"The documented observation for facet {index} was 3.03.",
        )
        records.append(
            ChatEvidenceRecord(
                record_id=f"common:article-{index}",
                origin="local_rag",
                evidence_level="abstract",
                scope="common",
                title=f"Study {index}",
                evidence_grade="A",
                passages=[passage],
            )
        )

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            cited = f"common:article-{min(self.calls, 3)}:chunk:1"
            statement = (
                "L'étude documente une observation."
                if self.calls <= 3
                else "L'étude documente une observation de 4,04."
            )
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [{"statement": statement, "evidence_ids": [cited]}],
                        "limitations": [],
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer_faceted(
        "Quel est l'impact de l'élevage en barrique sur les arômes et la structure ?", records
    )

    assert client.calls == 4
    assert result.generation_status == "partial_generated"
    assert result.cited_evidence_ids == [
        "common:article-1:chunk:1",
        "common:article-2:chunk:1",
        "common:article-3:chunk:1",
    ]
    assert "ne couvrent qu'une partie" in result.answer_markdown
    assert result.prompt_tokens == 200
    assert result.completion_tokens == 80
    failed = result.generation_traces[-1]
    assert failed.phase == "final_assembly"
    assert failed.outcome == "failed"
    assert failed.request_count == 1
    assert failed.validation_retries == 0
    assert failed.correction_temperature is None


def test_first_facet_is_corrected_without_preventing_later_facets() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:article-{index}",
            origin="local_rag",
            evidence_level="full_text",
            scope="common",
            article_id=f"article-{index}",
            title=f"Étude {index}",
            evidence_grade="A",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:article-{index}:chunk:1",
                    chunk_id=1,
                    page_start=1,
                    page_end=1,
                    text="L'étude documente un effet du traitement.",
                )
            ],
        )
        for index in range(1, 4)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            if self.calls <= 2:
                statement = "L'effet mesuré atteint 15 %."
                evidence_id = "common:article-1:chunk:1"
            else:
                statement = "L'étude documente un effet du traitement."
                evidence_id = f"common:article-{2 if self.calls in {3, 5} else 3}:chunk:1"
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": statement,
                                "evidence_ids": [evidence_id],
                            }
                        ],
                        "limitations": [],
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client).answer_faceted(
        "Quel est l'impact de l'élevage en barrique sur les arômes et la structure du Calvados ?",
        records,
    )

    assert client.calls == 6
    assert result.generation_status == "partial_generated"
    assert [draft.key for draft in result.facet_drafts] == [
        "aroma",
        "structure",
        "evolution",
    ]
    assert result.generation_traces[0].outcome == "generated"
    assert result.generation_traces[0].request_count == 3
    assert result.generation_traces[0].validation_retries == 2


@pytest.mark.parametrize(
    ("effort", "expected_calls"),
    [(AnswerEffort.DEEP, 4), (AnswerEffort.BALANCED, 4)],
)
def test_faceted_assembly_expands_once_when_effort_claim_threshold_is_validated(
    effort: AnswerEffort, expected_calls: int
) -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:article-{index}",
            origin="local_rag",
            evidence_level="full_text",
            scope="common",
            article_id=f"article-{index}",
            title=f"Étude {index}",
            evidence_grade="A",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:article-{index}:chunk:1",
                    chunk_id=1,
                    page_start=1,
                    page_end=1,
                    text="L'étude documente un effet mesuré.",
                )
            ],
        )
        for index in range(1, 4)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            evidence_id = f"common:article-{min(self.calls, 3)}:chunk:1"
            statements = (
                [
                    "L'\u00e9tude documente un effet mesur\u00e9.",
                    "L'\u00e9tude d\u00e9crit les conditions exp\u00e9rimentales.",
                ]
                if self.calls <= 3
                else ["L'\u00e9tude documente un effet mesur\u00e9."]
                if self.calls == 4
                else ["L'\u00e9tude documente un effet mesur\u00e9."] * 6
            )
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": "L'étude documente un effet mesuré.",
                                "evidence_ids": [evidence_id],
                            }
                            | {"statement": statement}
                            for statement in statements
                        ],
                        "limitations": [],
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client, answer_effort=effort).answer_faceted(
        "Quel est l'impact de l'élevage en barrique sur les arômes et la structure ?", records
    )

    assert client.calls == expected_calls
    assert result.generation_traces[-1].request_count == 1


@pytest.mark.parametrize("effort", [AnswerEffort.BALANCED, AnswerEffort.DEEP])
def test_faceted_assembly_reexpands_after_salvage_and_preserves_validated_drafts(
    effort: AnswerEffort,
) -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:article-{index}",
            origin="local_rag",
            evidence_level="full_text",
            scope="common",
            article_id=f"article-{index}",
            title=f"Étude {index}",
            evidence_grade="A",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:article-{index}:chunk:1",
                    chunk_id=1,
                    page_start=1,
                    page_end=1,
                    text=(
                        "L'étude documente un effet mesuré. "
                        "Les conditions expérimentales sont décrites."
                    ),
                )
            ],
        )
        for index in range(1, 4)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            evidence_id = f"common:article-{min(self.calls, 3)}:chunk:1"
            if self.calls <= 3:
                statements = [
                    "L'étude documente un effet mesuré.",
                    "Les conditions expérimentales sont décrites.",
                ]
            elif self.calls <= 5:
                statements = [
                    "L'étude documente un effet mesuré.",
                    "Les conditions expérimentales sont décrites.",
                    "L'étude documente un effet mesuré dans cette matrice.",
                    "Une valeur de 4,04 a été observée.",
                    "Une valeur de 5,05 a été observée.",
                    "Une valeur de 6,06 a été observée.",
                ]
            else:
                # The requested re-expansion still loses the valid facet claims.
                # The deterministic fallback must retain the six cited draft claims.
                statements = [
                    "L'étude documente un effet mesuré.",
                    "Les conditions expérimentales sont décrites.",
                    "L'étude documente un effet mesuré dans cette matrice.",
                ]
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {"statement": statement, "evidence_ids": [evidence_id]}
                            for statement in statements
                        ],
                        "limitations": [],
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderEvidenceRagService(client, answer_effort=effort).answer_faceted(
        "Quel est l'impact de l'élevage en barrique sur les arômes et la structure ?",
        records,
        axis_candidate_ids={
            "aroma": ["common:article-1"],
            "structure": ["common:article-2"],
            "evolution": ["common:article-3"],
        },
    )

    assert client.calls == 4
    assert len(result.answer.statements) == 6
    assert result.cited_evidence_ids == [
        "common:article-1:chunk:1",
        "common:article-2:chunk:1",
        "common:article-3:chunk:1",
    ]
    assert result.generation_status == "partial_generated"
    assert result.generation_traces[-1].request_count == 1
    assert result.generation_traces[-1].outcome == "partial_generated"


def test_faceted_renderer_keeps_each_axis_or_an_explicit_gap() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"common:article-{index}",
            origin="local_rag",
            evidence_level="full_text",
            scope="common",
            article_id=f"article-{index}",
            title=f"Étude {index}",
            evidence_grade="A",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"common:article-{index}:chunk:1",
                    chunk_id=1,
                    page_start=1,
                    page_end=1,
                    text="L'étude documente un effet mesuré.",
                )
            ],
        )
        for index in range(1, 4)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            if self.calls == 2:
                return _response(
                    json.dumps(
                        {
                            "status": "insufficient",
                            "response_format": "prose",
                            "statements": [],
                            "limitations": [],
                            "insufficiency_message": "Aucune preuve directe.",
                        }
                    )
                )
            cited = f"common:article-{min(self.calls, 3)}:chunk:1"
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": "L'étude documente un effet mesuré.",
                                "evidence_ids": [cited],
                            }
                        ],
                        "limitations": [],
                    }
                )
            )

    result = CiderEvidenceRagService(FakeClient()).answer_faceted(
        "Quel est l'impact de l'élevage en barrique sur les arômes et la structure ?", records
    )

    assert "Évolution chimique pendant la maturation — documenté" in result.answer_markdown
    assert "Structure, équilibre et perception en bouche — non documenté" in result.answer_markdown
    assert "Aucune preuve directe." in result.answer_markdown


def test_faceted_renderer_uses_validated_axis_coverage_status() -> None:
    records = [
        ChatEvidenceRecord(
            record_id=f"record-{index}",
            origin="local_rag",
            evidence_level="abstract",
            title=f"Study {index}",
            evidence_grade="A",
            passages=[
                ChatEvidencePassage(
                    evidence_id=f"record-{index}:abstract",
                    text="The study documents a measured effect.",
                )
            ],
        )
        for index in range(1, 3)
    ]
    facets = [
        ScientificFacet(
            key="effects",
            label="Effects",
            terms_fr=["effets"],
            terms_en=["effects"],
        ),
        ScientificFacet(
            key="conditions",
            label="Conditions",
            terms_fr=["conditions"],
            terms_en=["conditions"],
        ),
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            cited = "record-2:abstract" if self.calls == 2 else "record-1:abstract"
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": "The study documents a measured effect.",
                                "evidence_ids": [cited],
                            }
                        ],
                        "limitations": [],
                    }
                )
            )

    result = CiderEvidenceRagService(FakeClient()).answer_faceted(
        "Compare the effects and conditions of two treatments.",
        records,
        facets=facets,
        axis_coverage=[
            AxisCoverageAssessment(
                axis_key="effects",
                status="partial",
                supporting_candidate_ids=["record-1"],
                assessment="Only part of the axis is covered.",
            )
        ],
    )

    assert "## Effects" in result.answer_markdown
    assert "partially documented" in result.answer_markdown


def test_evidence_rag_salvages_only_valid_statement_after_repeated_failure() -> None:
    passage = ChatEvidencePassage(
        evidence_id="common:article-1:chunk:1",
        text="The observed value was 3.03.",
    )
    record = ChatEvidenceRecord(
        record_id="common:article-1",
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title="Grounded study",
        evidence_grade="A",
        passages=[passage],
    )

    class FakeClient:
        def chat(self, _messages, **_options):
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "definition": (
                            "La question porte sur la valeur observée dans cette étude. "
                            "La synthèse distingue le résultat documenté de toute valeur "
                            "non étayée."
                        ),
                        "statements": [
                            {
                                "statement": "La valeur observée était de 3,03.",
                                "evidence_ids": [passage.evidence_id],
                            },
                            {
                                "statement": "Une autre valeur était de 4,04.",
                                "evidence_ids": [passage.evidence_id],
                            },
                        ],
                        "limitations": [],
                    },
                    ensure_ascii=False,
                )
            )

    result = CiderEvidenceRagService(FakeClient()).answer("Quel est l'effet ?", [record])

    assert result.generation_status == "partial_generated"
    assert [item.statement for item in result.answer.statements] == [
        "La valeur observée était de 3,03."
    ]


def test_faceted_answer_salvage_discards_only_an_unsupported_numeric_statement() -> None:
    passage = ChatEvidencePassage(
        evidence_id="common:article-1:chunk:1",
        chunk_id=1,
        section="Results",
        page_start=1,
        page_end=1,
        text="The observed value was 3.03.",
    )
    record = ChatEvidenceRecord(
        record_id="common:article-1",
        origin="local_rag",
        evidence_level="full_text",
        scope="common",
        article_id="article-1",
        title="Grounded numeric study",
        authors=["Ada Test"],
        publication_year=2025,
        providers=["local"],
        passages=[passage],
    )
    answer = CiderEvidenceAnswer(
        statements=[
            CitedEvidenceStatement(
                statement="La valeur observée était de 3,03.",
                evidence_ids=[passage.evidence_id],
            ),
            CitedEvidenceStatement(
                statement="Une autre valeur était de 4,04.",
                evidence_ids=[passage.evidence_id],
            ),
        ],
        limitations=[],
    )

    salvaged = _salvage_grounded_evidence_answer(
        answer,
        {passage.evidence_id: (record, passage)},
        {passage.evidence_id},
        ResponseStyle.PROSE,
    )

    assert salvaged is not None
    assert [statement.statement for statement in salvaged.statements] == [
        "La valeur observée était de 3,03."
    ]
    assert "preuves pertinentes retenues" in salvaged.limitations[-1]
    assert "générées" not in salvaged.limitations[-1]


@pytest.mark.parametrize(
    "leak",
    [
        "Le RAG n'a retenu qu'une source.",
        "ARGO a validé cette réponse.",
        "Click and Read pour ouvrir l'article.",
        "Aucun chiffre n'a été ajouté.",
        "Les consignes internes imposent cette limite.",
        "Le validateur automatique a écarté ce passage.",
        "Le filtrage sémantique a retenu deux études.",
        "Le processus de contrôle a écarté cette source.",
        "Le contrôle de fidélité est satisfaisant.",
    ],
)
def test_reader_facing_answer_rejects_internal_process_leaks(leak: str) -> None:
    with pytest.raises(RuntimeError, match="internal generation"):
        _reject_internal_process_leaks([leak])


def test_abstract_rag_prompt_keeps_grounding_controls_silent_and_retries_leak() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, **_options):
            self.calls += 1
            system = messages[0]["content"]
            assert "ne mentionne jamais RAG, ARGO" in system
            assert "contrôles de fidélité sont silencieux" in system
            statement = (
                "Le RAG a vérifié que les levures influencent la fermentation."
                if self.calls == 1
                else "Les levures influencent la fermentation."
            )
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [{"statement": statement, "record_ids": [record.record_id]}],
                        "limitations": [],
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderAbstractRagService(client).answer("Question", [record])

    assert client.calls == 2
    assert "RAG" not in result.answer_markdown
    assert "Les levures influencent" in result.answer_markdown


def test_pilot_rag_uses_bullets_only_when_the_requested_format_is_explicit() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")

    class FakeClient:
        def chat(self, _messages, **_options):
            return _response(
                json.dumps(
                    {
                        "response_format": "bullet_list",
                        "statements": [
                            {
                                "statement": "Surveiller les levures.",
                                "record_ids": [record.record_id],
                            }
                        ],
                        "limitations": ["Cette réponse repose sur un résumé bibliographique."],
                    },
                    ensure_ascii=False,
                )
            )

    result = CiderAbstractRagService(FakeClient()).answer(
        "Réponds sous forme de liste à puces.", [record]
    )

    assert result.answer.response_format == "bullet_list"
    assert result.answer_markdown.startswith("- Surveiller les levures.")


def test_pilot_rag_renders_one_non_empty_bullet_per_statement() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")

    class FakeClient:
        def chat(self, _messages, **_options):
            return _response(
                json.dumps(
                    {
                        "response_format": "bullet_list",
                        "statements": [
                            {
                                "statement": "Surveiller les levures.",
                                "record_ids": [record.record_id],
                            },
                            {
                                "statement": "Observer les bactéries.",
                                "record_ids": [record.record_id],
                            },
                        ],
                        "limitations": [],
                    }
                )
            )

    result = CiderAbstractRagService(FakeClient()).answer(
        "Liste les microorganismes à surveiller.", [record]
    )

    answer_body = result.answer_markdown.split("\n\n## Références", 1)[0]
    bullets = [line for line in answer_body.splitlines() if line.strip()]
    assert len(bullets) == 2
    assert all(line.startswith("- ") and line[2:].strip() for line in bullets)
    assert result.answer_markdown.count("Test, A. (2025).") == 1


def test_pilot_rag_orders_final_bibliography() -> None:
    zulu = _record("11111111-1111-1111-1111-111111111111", "10.1000/zulu")
    zulu.authors = ["Zoé Zulu"]
    zulu.title = "Zulu study"
    alpha = _record("22222222-2222-2222-2222-222222222222", "10.1000/alpha")
    alpha.authors = ["Anne Alpha"]
    alpha.title = "Alpha study"

    class FakeClient:
        def chat(self, _messages, **_options):
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": "Les microorganismes influencent la fermentation.",
                                "record_ids": [zulu.record_id, alpha.record_id],
                            }
                        ],
                        "limitations": [],
                    }
                )
            )

    result = CiderAbstractRagService(FakeClient()).answer("Question", [zulu, alpha])
    references = result.answer_markdown.split("## Références", 1)[1]

    assert references.index("Alpha, A.") < references.index("Zulu, Z.")


def test_complete_scientific_prose_response_contract() -> None:
    first = _record("11111111-1111-1111-1111-111111111111", "10.1000/first")
    second = _record("22222222-2222-2222-2222-222222222222", "10.1000/second")
    second.authors = ["Bob Doe"]
    second.title = "Bacterial activity in cider"

    class FakeClient:
        def chat(self, _messages, *, json_schema, **_options):
            assert json_schema["properties"]["response_format"]["enum"] == [
                "prose",
                "bullet_list",
            ]
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": (
                                    "Les levures et les bactéries influencent la fermentation."
                                ),
                                "record_ids": [first.record_id, second.record_id],
                            },
                            {
                                "statement": "Les abstracts ne décrivent pas tous les mécanismes.",
                                "record_ids": [second.record_id],
                            },
                        ],
                        "limitations": [
                            "La réponse repose uniquement sur des résumés bibliographiques."
                        ],
                    },
                    ensure_ascii=False,
                )
            )

    result = CiderAbstractRagService(FakeClient()).answer(
        "Quel rôle jouent les microorganismes ?", [first, second]
    )
    body, references = result.answer_markdown.split("\n\n## Références\n\n", 1)

    assert result.answer.response_format == "prose"
    assert "(Test, 2025); (Doe, 2025)" in body
    assert "La réponse repose uniquement" in body
    assert all(
        not paragraph.lstrip().startswith(("-", "*", "•")) for paragraph in body.split("\n\n")
    )
    assert references.count("10.1000/first") == 1
    assert references.count("10.1000/second") == 1


def test_pilot_rag_rejects_bullets_when_prose_is_explicitly_requested() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")

    class FakeClient:
        def chat(self, _messages, **_options):
            return _response(
                json.dumps(
                    {
                        "response_format": "bullet_list",
                        "statements": [
                            {
                                "statement": "Surveiller les levures.",
                                "record_ids": [record.record_id],
                            }
                        ],
                        "limitations": [],
                    }
                )
            )

    with pytest.raises(RuntimeError, match="response style"):
        CiderAbstractRagService(FakeClient()).answer("Réponds en prose, sans puces.", [record])


@pytest.mark.parametrize("marker", ["-", "*", "•"])
def test_pilot_rag_rejects_list_marker_in_each_prose_paragraph(marker: str) -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")

    class FakeClient:
        def chat(self, _messages, **_options):
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": f"Premier paragraphe.\n\n{marker} Second paragraphe.",
                                "record_ids": [record.record_id],
                            }
                        ],
                        "limitations": [],
                    },
                    ensure_ascii=False,
                )
            )

    with pytest.raises(RuntimeError, match="list marker"):
        CiderAbstractRagService(FakeClient()).answer("Réponds en prose.", [record])


def test_pilot_rag_rejects_an_emoji() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")

    class FakeClient:
        def chat(self, _messages, **_options):
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": "Les levures influencent la fermentation. 🧪",
                                "record_ids": [record.record_id],
                            }
                        ],
                        "limitations": [],
                    },
                    ensure_ascii=False,
                )
            )

    with pytest.raises(RuntimeError, match="emoji"):
        CiderAbstractRagService(FakeClient()).answer("Question", [record])


def test_pilot_rag_uses_the_full_structural_correction_budget() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": "- Fragment en liste.",
                                "record_ids": [record.record_id],
                            }
                        ],
                        "limitations": [],
                    }
                )
            )

    client = FakeClient()
    with pytest.raises(RuntimeError, match="list marker"):
        CiderAbstractRagService(client).answer("Réponds en prose.", [record])

    assert client.calls == 10


@pytest.mark.parametrize(
    ("grade", "source_text", "statement"),
    [
        (
            "A",
            "Yeast growth was observed in cider.",
            "Dans le cidre, une croissance des levures est observée.",
        ),
        (
            "B",
            "Yeast growth was observed in red wine.",
            "Dans le vin rouge, une croissance des levures est observée.",
        ),
        (
            "B",
            "Yeast growth was observed in wine and apple juice.",
            "Une croissance des levures est observée dans le vin et dans le jus de pomme.",
        ),
        (
            "B",
            "Yeast growth was observed. The substrate was not specified.",
            "Une croissance des levures est observée ; la matrice n’est pas précisée.",
        ),
    ],
)
def test_results_start_with_documented_study_context_without_preamble(
    grade, source_text, statement
):
    record = ChatEvidenceRecord(
        record_id="common:matrix",
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title="Experimental yeast growth",
        evidence_grade=grade,
        passages=[ChatEvidencePassage(evidence_id="matrix:abstract", text=source_text)],
    )

    class Client:
        calls = 0

        def chat(self, messages, **_options):
            self.calls += 1
            assert "definition est facultatif" in messages[0]["content"]
            assert "si la matrice est inconnue, ne l'invente pas" in messages[0]["content"]
            return _response(
                json.dumps(
                    {
                        "status": "answerable",
                        "definition": None,
                        "statements": [
                            {"statement": statement, "evidence_ids": ["matrix:abstract"]}
                        ],
                        "limitations": [],
                        "insufficiency_message": None,
                    },
                    ensure_ascii=False,
                )
            )

    client = Client()
    result = CiderEvidenceRagService(client).answer("Que montrent les observations ?", [record])
    assert result.answer_markdown.startswith(statement)
    assert "Preuve indirecte" not in result.answer_markdown
    assert result.cited_evidence_ids == ["matrix:abstract"]
    assert client.calls == 2


def test_pilot_rag_rejects_known_empty_introduction() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")

    class FakeClient:
        def chat(self, _messages, **_options):
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": (
                                    "Excellente question. Les levures influencent la fermentation."
                                ),
                                "record_ids": [record.record_id],
                            }
                        ],
                        "limitations": [],
                    },
                    ensure_ascii=False,
                )
            )

    with pytest.raises(RuntimeError, match="empty introduction"):
        CiderAbstractRagService(FakeClient()).answer("Question", [record])


def test_pilot_rag_rejects_a_citation_outside_supplied_records() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")

    class FakeClient:
        def chat(self, _messages, **_options):
            return _response(
                json.dumps(
                    {
                        "statements": [
                            {
                                "statement": "Unsupported claim",
                                "record_ids": ["22222222-2222-2222-2222-222222222222"],
                            }
                        ],
                        "limitations": [],
                    }
                )
            )

    with pytest.raises(RuntimeError, match="outside"):
        CiderAbstractRagService(FakeClient()).answer("Question", [record])


def test_pilot_rag_retries_an_unsupported_normative_claim() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")
    record.abstract = "The experimental cider contained less than 200 mg/L methanol."

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0
            self.temperatures: list[float | None] = []

        def chat(self, _messages, **_options):
            self.calls += 1
            self.temperatures.append(_options.get("temperature"))
            statement = (
                "Le méthanol doit rester sous 200 mg/L pour respecter les normes."
                if self.calls == 1
                else "Dans cette expérience, le méthanol était inférieur à 200 mg/L."
            )
            return _response(
                json.dumps(
                    {
                        "statements": [{"statement": statement, "record_ids": [record.record_id]}],
                        "limitations": [],
                    },
                    ensure_ascii=False,
                )
            )

    client = FakeClient()
    result = CiderAbstractRagService(client).answer("Question", [record])

    assert client.calls == 2
    assert client.temperatures == [None, 0.1]
    assert "Dans cette expérience" in result.answer_markdown
    assert result.prompt_tokens == 100
    assert result.generation_traces[0].validation_retries == 1
    assert result.generation_traces[0].correction_temperature == 0.1


@pytest.mark.parametrize("temperature", [-0.01, 0.201, 1.0])
def test_scientific_correction_temperature_is_bounded(temperature: float) -> None:
    class FakeClient:
        def chat(self, _messages, **_options):
            raise AssertionError("invalid configuration must fail before generation")

    with pytest.raises(ValueError, match="correction temperature"):
        CiderAbstractRagService(FakeClient(), correction_temperature=temperature)
    with pytest.raises(ValueError, match="correction temperature"):
        CiderEvidenceRagService(FakeClient(), correction_temperature=temperature)


@pytest.mark.parametrize(
    ("statement", "error"),
    [
        (
            "Le résultat 11111111 montre une fermentation cidricole.",
            "record id",
        ),
        (
            "Une teneur supérieure à 200 mg/L serait indésirable pour la sécurité.",
            "safety",
        ),
    ],
)
def test_pilot_rag_rejects_leaked_ids_and_unsupported_safety_claims(
    statement: str,
    error: str,
) -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")
    record.abstract = "The experimental cider contained less than 200 mg/L methanol."

    class FakeClient:
        def chat(self, _messages, **_options):
            return _response(
                json.dumps(
                    {
                        "statements": [{"statement": statement, "record_ids": [record.record_id]}],
                        "limitations": [],
                    },
                    ensure_ascii=False,
                )
            )

    with pytest.raises(RuntimeError, match=error):
        CiderAbstractRagService(FakeClient()).answer("Question", [record])


def test_pilot_rag_retries_one_length_truncation() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, **_options):
            self.calls += 1
            if self.calls == 1:
                raise ArgoProtocolError("no content (finish_reason=length)")
            return _response(
                json.dumps(
                    {
                        "statements": [
                            {
                                "statement": "Les levures influencent la fermentation.",
                                "record_ids": [record.record_id],
                            }
                        ],
                        "limitations": [],
                    }
                )
            )

    client = FakeClient()
    result = CiderAbstractRagService(client).answer("Question", [record])

    assert client.calls == 2
    assert result.prompt_tokens == 50
