from __future__ import annotations

import json

import pytest

from app.llm.argo_client import ArgoProtocolError, ArgoScientificValidationError
from app.llm.contracts import GenerationMetrics, GenerationResponse
from app.llm.response_style import ResponseStyle
from app.models.chatbot import ChatEvidencePassage, ChatEvidenceRecord
from app.updates.pilot_rag import (
    CiderAbstractRagService,
    CiderEvidenceAnswer,
    CiderEvidenceRagService,
    CitedEvidenceStatement,
    _apa_reference,
    _clean_author_names,
    _reject_internal_process_leaks,
    _renderable_doi,
    _salvage_grounded_evidence_answer,
)
from app.updates.vector_index import BibliographicHybridResult


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
                        "definition": "Effet du procédé demandé.",
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
    assert "Preuve indirecte" in result.answer_markdown
    assert "Définition retenue" not in result.answer_markdown
    assert "Effet du procédé demandé." not in result.answer_markdown
    assert "## Réponse synthétique" in result.answer_markdown
    assert "## Effets documentés" in result.answer_markdown
    assert "## Limites des preuves" in result.answer_markdown
    assert "Related downstream process" in result.answer_markdown
    assert "## Références" in result.answer_markdown


def test_evidence_rag_recovers_empty_answerable_as_documentary_abstention() -> None:
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
            assert "utilise status=insufficient" in messages[-1]["content"]
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
    result = CiderEvidenceRagService(client).answer(
        "Quel agent de clarification est le plus efficace ?",
        [record],
    )

    assert client.calls == 2
    assert result.answer.status == "insufficient"
    assert result.generation_status == "abstained"
    assert result.answer.statements == []
    assert result.cited_evidence_ids == []
    assert result.source_record_ids == []
    assert result.model == "chat-gpt-oss-20b"
    assert "ne permettent pas de comparer directement" in result.answer_markdown
    assert "Aucune référence n'est citée." in result.answer_markdown
    assert "Adjacent clarification process" not in result.answer_markdown
    assert result.generation_traces[0].model_dump() == {
        "schema_version": 1,
        "phase": "evidence",
        "outcome": "abstained",
        "request_count": 2,
        "validation_retries": 1,
        "length_retries": 0,
        "correction_temperature": 0.1,
        "prompt_tokens": 100,
        "completion_tokens": 40,
    }


def test_pilot_rag_constrains_ids_and_renders_abstract_citations() -> None:
    record = _record("11111111-1111-1111-1111-111111111111", "10.1000/cider")

    class FakeClient:
        def chat(self, messages, *, json_schema, max_output_tokens):
            assert max_output_tokens == 4096
            assert json_schema["properties"]["response_format"] == {
                "type": "string",
                "const": "prose",
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
            assert "response_format=bullet_list seulement" in messages[0]["content"]
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
    assert result.prompt_tokens == 50


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
                definition = "Le vieillissement sous bois est le procédé étudié."
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
    assert result.answer.definition == "Le vieillissement sous bois est le procédé étudié."
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
        def chat(self, messages, *, json_schema, max_output_tokens):
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

    result = CiderEvidenceRagService(FakeClient()).answer(
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


def test_evidence_rag_exhausted_grounding_retries_raise_worker_safe_error() -> None:
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
            assert enum == [
                "common:article-1:chunk:1",
                "common:article-2:chunk:1",
                "common:article-3:chunk:1",
            ]
            if self.calls <= 3:
                assert max_output_tokens == 4096
                assert json_schema["properties"]["statements"]["maxItems"] == 4
                assert "facet_drafts" not in payload
                cited = enum[self.calls - 1]
            else:
                assert max_output_tokens == 6144
                assert json_schema["properties"]["statements"]["maxItems"] == 16
                assert len(payload["facet_drafts"]) == 3
                assert "A=direct, B=indirect" in messages[0]["content"]
                assert "N'utilise jamais C ou D comme preuve" in messages[0]["content"]
                assert "status=insufficient" in messages[0]["content"]
                assert "status=answerable avec statements vide" in messages[0]["content"]
                cited = enum[0]
            return _response(
                json.dumps(
                    {
                        "response_format": "prose",
                        "statements": [
                            {
                                "statement": "L'étude observe un effet documenté.",
                                "evidence_ids": [cited],
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

    assert client.calls == 4
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
    assert all(trace.request_count == 1 for trace in result.generation_traces)
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

    assert client.calls == 5
    assert result.generation_status == "partial_generated"
    assert result.cited_evidence_ids == [
        "common:article-1:chunk:1",
        "common:article-2:chunk:1",
        "common:article-3:chunk:1",
    ]
    assert "ne couvrent qu'une partie" in result.answer_markdown
    assert result.prompt_tokens == 250
    assert result.completion_tokens == 100
    failed = result.generation_traces[-1]
    assert failed.phase == "final_assembly"
    assert failed.outcome == "failed"
    assert failed.request_count == 2
    assert failed.validation_retries == 1
    assert failed.correction_temperature == 0.1


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
    assert "preuves disponibles" in salvaged.limitations[-1]
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
            assert json_schema["properties"]["response_format"]["const"] == "prose"
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


def test_pilot_rag_attempts_only_one_structural_correction() -> None:
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
