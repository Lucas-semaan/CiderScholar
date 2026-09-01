from __future__ import annotations

import json

import pytest

from app.llm.argo_client import ArgoProtocolError
from app.llm.contracts import GenerationMetrics, GenerationResponse
from app.retrieval.query_planning import (
    ArgoQueryPlanningService,
    QueryPlanningProtocolDiagnostic,
    ResearchQueryPlan,
    deterministic_query_plan,
)


def _response(
    content: dict[str, object] | str,
    *,
    done_reason: str = "stop",
) -> GenerationResponse:
    return GenerationResponse(
        model="planner-test",
        content=json.dumps(content) if isinstance(content, dict) else content,
        done_reason=done_reason,
        metrics=GenerationMetrics(
            total_duration_seconds=0.1,
            load_duration_seconds=0,
            prompt_eval_count=120,
            prompt_eval_duration_seconds=0.01,
            eval_count=80,
            eval_duration_seconds=0.01,
        ),
    )


class _FakePlanningClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.messages = None
        self.options = None

    def chat(self, messages, **options):
        self.messages = messages
        self.options = options
        return _response(self.payload)


class _SequencePlanningClient:
    def __init__(self, responses: list[GenerationResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[object, dict[str, object]]] = []

    def chat(self, messages, **options):
        self.calls.append((messages, options))
        return self.responses[len(self.calls) - 1]


def test_argo_planner_keeps_a_simple_question_on_one_axis() -> None:
    payload = {
        "interpreted_question": "Influence de la température sur la fermentation du cidre",
        "concept_definition": "Fermentation du cidre sous controle de temperature.",
        "ambiguities": ["temperature de fermentation ou de stockage"],
        "excluded_concepts": ["vinification"],
        "requires_faceted_answer": False,
        "matrix_primary": ["cider"],
        "matrix_close": [],
        "matrix_distant": ["apple juice", "wine"],
        "process_terms_fr": ["fermentation"],
        "process_terms_en": ["fermentation"],
        "axes": [
            {
                "key": "temperature",
                "label": "Température de fermentation",
                "question": "Comment la température modifie-t-elle la fermentation du cidre ?",
                "terms_fr": ["température", "cinétique"],
                "terms_en": ["temperature", "fermentation kinetics"],
                "search_queries": [
                    "cider\u200b fermentation temperature kinetics",
                    "cider fermentation temperature experiment",
                ],
            }
        ],
        "retrieval_queries": ["cider fermentation temperature kinetics"],
    }
    client = _FakePlanningClient(payload)

    result = ArgoQueryPlanningService(client).plan(
        "Quel est l'effet de la température sur la fermentation du cidre ?"
    )

    assert result.plan.requires_faceted_answer is False
    assert result.plan.concept_definition == "Fermentation du cidre sous controle de temperature."
    assert result.plan.ambiguities == ["temperature de fermentation ou de stockage"]
    assert result.plan.excluded_concepts == ["vinification"]
    assert [axis.key for axis in result.plan.axes] == ["temperature"]
    assert result.plan.axes[0].search_queries == [
        "cider fermentation temperature kinetics",
        "cider fermentation temperature experiment",
    ]
    assert result.prompt_tokens == 120
    assert client.options["json_schema"] == ResearchQueryPlan.model_json_schema()
    assert client.options["max_output_tokens"] == 1800
    prompt = client.messages[0]["content"]
    assert "concept_definition" in prompt
    assert "faux amis" in prompt
    assert "excluded_concepts" in prompt
    assert "La décomposition n'est jamais systématique" in client.messages[0]["content"]


def test_argo_planner_deep_mode_instructs_conditional_multi_axis_decomposition() -> None:
    client = _FakePlanningClient(
        {
            "interpreted_question": "Effet du traitement sur le jus de pomme",
            "axes": [
                {
                    "key": "treatment",
                    "label": "Effet du traitement",
                    "question": "Quel est l'effet du traitement sur le jus de pomme ?",
                    "terms_fr": ["traitement"],
                    "terms_en": ["treatment"],
                    "search_queries": ["apple juice treatment effect"],
                }
            ],
            "retrieval_queries": ["apple juice treatment effect"],
        }
    )

    result = ArgoQueryPlanningService(client).plan(
        "Effet du traitement sur le jus de pomme ?", deep=True
    )

    assert len(result.plan.axes) == 1
    prompt = client.messages[0]["content"]
    assert "finalité/effets, mécanismes et conditions, comparaison" in prompt
    assert "Ne sur-décompose jamais une question simple" in prompt


def test_argo_planner_prepends_controlled_process_query_when_generated_plan_drifts() -> None:
    client = _FakePlanningClient(
        {
            "interpreted_question": "Impact du cuvage cidricole",
            "axes": [
                {
                    "key": "overall",
                    "label": "Impact global",
                    "question": "Quel est l’impact du cuvage ?",
                    "terms_fr": ["cuvage"],
                    "terms_en": ["holding"],
                    "search_queries": [
                        "apple juice clarification storage quality",
                        "cider fermentation temperature quality",
                    ],
                }
            ],
            "retrieval_queries": ["apple juice clarification storage quality"],
        }
    )

    result = ArgoQueryPlanningService(client).plan(
        "Quel est l’impact du cuvage pour les jus de pomme et les cidres ?",
        deep=True,
    )

    first_query = result.plan.axes[0].search_queries[0]
    assert "apple juice" in first_query
    assert "cider" in first_query
    assert "mash maceration" in first_query
    assert "pre-press maceration" in first_query
    assert result.plan.retrieval_queries[0] == first_query


def test_argo_planner_deep_mode_completes_a_comparative_plan_with_fallback_axes() -> None:
    client = _FakePlanningClient(
        {
            "interpreted_question": "Comparer les colles végétales et animales",
            "axes": [
                {
                    "key": "fining_effects",
                    "label": "Effets du collage",
                    "question": "Quels sont les effets du collage ?",
                    "terms_fr": ["collage"],
                    "terms_en": ["fining"],
                    "search_queries": [
                        "apple juice fining effects",
                        "apple juice fining turbidity",
                        "apple juice fining clarity",
                        "apple juice colloidal stability",
                    ],
                }
            ],
            "retrieval_queries": ["apple juice fining effects"],
        }
    )

    result = ArgoQueryPlanningService(client).plan(
        "Comparer les colles végétales et animales dans le jus de pomme.", deep=True
    )

    assert [axis.key for axis in result.plan.axes] == [
        "fining_effects",
        "fining_agents_comparison",
        "fining_mechanisms_conditions",
        "fining_tradeoffs_limits",
    ]
    assert result.plan.requires_faceted_answer is True
    assert any("plant proteins" in query for query in result.plan.retrieval_queries)


def test_argo_planner_balanced_mode_restores_mineral_fining_axis() -> None:
    client = _FakePlanningClient(
        {
            "interpreted_question": "Comparer les colles végétales et animales",
            "axes": [
                {
                    "key": "fining_effects",
                    "label": "Effets du collage",
                    "question": "Quels sont les effets du collage ?",
                    "terms_fr": ["collage"],
                    "terms_en": ["fining"],
                    "search_queries": ["apple juice fining effects"],
                },
                {
                    "key": "fining_agents_comparison",
                    "label": "Comparaison des colles",
                    "question": "Comment comparer les protéines végétales et animales ?",
                    "terms_fr": ["proteines vegetales", "gelatine"],
                    "terms_en": ["plant proteins", "gelatin"],
                    "search_queries": [
                        "apple juice plant proteins gelatin fining",
                        "apple juice animal plant fining agents",
                        "apple juice pea protein gelatin",
                        "apple juice alternative fining proteins",
                    ],
                },
            ],
            "retrieval_queries": ["apple juice plant proteins gelatin fining"],
        }
    )

    result = ArgoQueryPlanningService(client).plan(
        "Comparer les colles végétales et animales dans le jus de pomme."
    )

    assert [axis.key for axis in result.plan.axes] == [
        "fining_effects",
        "fining_agents_comparison",
        "fining_mechanisms_conditions",
    ]
    mineral_axis = result.plan.axes[-1]
    assert "bentonite" in mineral_axis.terms_en
    assert any("bentonite" in query for query in mineral_axis.search_queries)
    assert any("bentonite" in query for query in result.plan.retrieval_queries)


def test_argo_planner_replaces_peripheral_full_plan_axis_with_required_facet() -> None:
    client = _FakePlanningClient(
        {
            "interpreted_question": "Comparer les colles végétales et animales",
            "axes": [
                {
                    "key": "fining_effects",
                    "label": "Effets du collage",
                    "question": "Quels sont les effets du collage ?",
                    "terms_fr": ["collage"],
                    "terms_en": ["fining"],
                    "search_queries": ["apple juice fining effects"],
                },
                {
                    "key": "fining_agents_comparison",
                    "label": "Comparaison des colles",
                    "question": "Comment comparer les protéines végétales et animales ?",
                    "terms_fr": ["proteines vegetales", "gelatine"],
                    "terms_en": ["plant proteins", "gelatin"],
                    "search_queries": ["apple juice plant proteins gelatin fining"],
                },
                {
                    "key": "consumer_context",
                    "label": "Contexte consommateur",
                    "question": "Quel contexte consommateur accompagne le collage ?",
                    "terms_fr": ["consommateur"],
                    "terms_en": ["consumer"],
                    "search_queries": ["apple juice fining consumer context"],
                },
                {
                    "key": "market_context",
                    "label": "Contexte de marché",
                    "question": "Quel contexte de marché accompagne le collage ?",
                    "terms_fr": ["marché"],
                    "terms_en": ["market"],
                    "search_queries": ["apple juice fining market context"],
                },
            ],
            "retrieval_queries": ["apple juice plant proteins gelatin fining"],
        }
    )

    result = ArgoQueryPlanningService(client).plan(
        "Comparer les colles végétales et animales dans le jus de pomme."
    )

    assert [axis.key for axis in result.plan.axes] == [
        "fining_effects",
        "fining_agents_comparison",
        "fining_mechanisms_conditions",
        "consumer_context",
    ]
    assert len(result.plan.axes) == 4
    assert "bentonite" in result.plan.axes[2].terms_en
    assert "market_context" not in {axis.key for axis in result.plan.axes}


def test_argo_planner_balanced_mode_prioritizes_required_deterministic_axis() -> None:
    client = _FakePlanningClient(
        {
            "interpreted_question": "Stabilité protéique pendant le stockage",
            "axes": [
                {
                    "key": f"peripheral_axis_{index}",
                    "label": f"Axe périphérique {index}",
                    "question": f"Quelle dimension périphérique {index} étudier ?",
                    "terms_fr": [f"périphérique {index}"],
                    "terms_en": [f"peripheral {index}"],
                    "search_queries": [f"apple juice peripheral dimension {index}"],
                }
                for index in range(1, 5)
            ],
            "retrieval_queries": ["apple juice storage protein stability"],
        }
    )

    result = ArgoQueryPlanningService(client).plan(
        "Comment la température et la durée de stockage modifient-elles la stabilité "
        "protéique du jus de pomme ?"
    )

    assert [axis.key for axis in result.plan.axes] == [
        "protein_stability",
        "peripheral_axis_1",
        "peripheral_axis_2",
        "peripheral_axis_3",
    ]


def test_argo_planner_can_choose_two_independent_axes_and_expand_the_matrix() -> None:
    payload = {
        "interpreted_question": "Effets du bois sur les arômes et la structure du Calvados",
        "requires_faceted_answer": True,
        "matrix_primary": ["Calvados"],
        "matrix_close": ["apple brandy", "apple spirit", "cider brandy"],
        "matrix_distant": ["fruit brandy", "wine"],
        "process_terms_fr": ["élevage en barrique", "chêne"],
        "process_terms_en": ["barrel aging", "oak aging"],
        "axes": [
            {
                "key": "aroma",
                "label": "Arômes",
                "question": "Quels composés volatils évoluent pendant l'élevage sous bois ?",
                "terms_fr": ["arômes", "composés volatils", "esters"],
                "terms_en": ["aroma", "volatile compounds", "esters"],
                "search_queries": [
                    "apple brandy oak aging volatile compounds esters",
                    "apple brandy barrel aging aroma",
                ],
            },
            {
                "key": "structure",
                "label": "Structure en bouche",
                "question": "Quels changements structuraux sont dus au bois ?",
                "terms_fr": ["polyphénols", "tanins", "acidité", "couleur"],
                "terms_en": ["phenolics", "tannins", "acidity", "color", "mouthfeel"],
                "search_queries": [
                    "cider brandy wood aging phenolics tannins acidity color mouthfeel",
                    "cider brandy oak aging tannins",
                ],
            },
        ],
        "retrieval_queries": ["Calvados barrel aging aroma structure"],
    }

    result = ArgoQueryPlanningService(_FakePlanningClient(payload)).plan(
        "Impact de la barrique sur les arômes et la structure du Calvados ?"
    )
    intent = result.plan.scientific_intent(
        "Impact de la barrique sur les arômes et la structure du Calvados ?"
    )

    assert result.plan.requires_faceted_answer is True
    assert [axis.key for axis in result.plan.axes] == ["aroma", "structure", "evolution"]
    assert "apple brandy oak aging volatile compounds esters" in result.plan.retrieval_queries
    assert (
        "cider brandy wood aging phenolics tannins acidity color mouthfeel"
        in result.plan.retrieval_queries
    )
    assert intent.matrix_close[:2] == ["apple brandy", "apple spirit"]
    assert [facet.key for facet in intent.facets] == ["aroma", "structure", "evolution"]
    assert "phenolic compounds" in intent.facet("structure").terms_en


@pytest.mark.parametrize(
    ("generated_value", "axis_count", "expected_value"),
    [(False, 2, True), (True, 1, False)],
)
def test_argo_planner_derives_faceted_flag_from_the_validated_axes(
    generated_value: bool,
    axis_count: int,
    expected_value: bool,
) -> None:
    axes = [
        {
            "key": f"comparison_{index}",
            "label": f"Comparison {index}",
            "question": f"How does treatment {index} affect apple juice?",
            "terms_fr": [f"traitement {index}"],
            "terms_en": [f"treatment {index}"],
            "search_queries": [f"apple juice treatment {index}"],
        }
        for index in range(1, axis_count + 1)
    ]
    payload = {
        "interpreted_question": "Comparer deux traitements du jus de pomme",
        "requires_faceted_answer": generated_value,
        "axes": axes,
        "retrieval_queries": ["apple juice treatment comparison"],
    }

    result = ArgoQueryPlanningService(_FakePlanningClient(payload)).plan(
        "Quel est l'intérêt des traitements du jus de pomme ?"
    )

    assert result.plan.requires_faceted_answer is expected_value
    assert len(result.plan.axes) == axis_count


def test_argo_planner_derives_faceted_flag_when_argo_omits_the_redundant_field() -> None:
    payload = {
        "interpreted_question": "Effet du traitement sur le jus de pomme",
        "axes": [
            {
                "key": "treatment",
                "label": "Effet du traitement",
                "question": "Quel est l'effet du traitement sur le jus de pomme ?",
                "terms_fr": ["traitement"],
                "terms_en": ["treatment"],
                "search_queries": ["apple juice treatment effect"],
            }
        ],
        "retrieval_queries": ["apple juice treatment effect"],
    }

    result = ArgoQueryPlanningService(_FakePlanningClient(payload)).plan(
        "Effet du traitement sur le jus de pomme ?"
    )

    assert result.plan.requires_faceted_answer is False


def test_argo_planner_accepts_a_whole_document_json_fence() -> None:
    payload = {
        "interpreted_question": "Effet du traitement sur le jus de pomme",
        "axes": [
            {
                "key": "treatment",
                "label": "Effet du traitement",
                "question": "Quel est l'effet du traitement sur le jus de pomme ?",
                "terms_fr": ["traitement"],
                "terms_en": ["treatment"],
                "search_queries": ["apple juice treatment effect"],
            }
        ],
        "retrieval_queries": ["apple juice treatment effect"],
    }
    client = _SequencePlanningClient([_response(f"```json\n{json.dumps(payload)}\n```")])

    result = ArgoQueryPlanningService(client).plan("Effet du traitement sur le jus de pomme ?")

    assert result.plan.axes[0].key == "treatment"


def test_argo_planner_expands_the_retry_budget_after_a_truncated_plan() -> None:
    valid_payload = {
        "interpreted_question": "Effet du traitement sur le jus de pomme",
        "axes": [
            {
                "key": "treatment",
                "label": "Effet du traitement",
                "question": "Quel est l'effet du traitement sur le jus de pomme ?",
                "terms_fr": ["traitement"],
                "terms_en": ["treatment"],
                "search_queries": ["apple juice treatment effect"],
            }
        ],
        "retrieval_queries": ["apple juice treatment effect"],
    }
    client = _SequencePlanningClient(
        [
            _response('{"interpreted_question": "plan tronqué"', done_reason="length"),
            _response(valid_payload),
        ]
    )

    result = ArgoQueryPlanningService(client, max_output_tokens=4096).plan(
        "Effet du traitement sur le jus de pomme ?"
    )

    assert result.plan.axes[0].key == "treatment"
    assert [call[1]["max_output_tokens"] for call in client.calls] == [1800, 3200]
    assert "tronqué ou invalide" in client.calls[1][0][-1]["content"]


def test_argo_planner_reports_repeated_invalid_outputs_as_a_protocol_error() -> None:
    client = _FakePlanningClient({"requires_faceted_answer": False})

    with pytest.raises(ArgoProtocolError) as error:
        ArgoQueryPlanningService(client).plan("Effet du traitement sur le jus de pomme ?")

    assert str(error.value) == "ARGO returned an invalid research query plan"
    assert isinstance(error.value.__cause__, Exception)


@pytest.mark.parametrize(
    ("responses", "expected_diagnostic"),
    [
        (
            [_response('{"interpreted_question": "truncated"'), _response('{"axes":')],
            {"category": "invalid_json", "pydantic_path": None, "pydantic_type": None},
        ),
        (
            [
                _response({"requires_faceted_answer": False}),
                _response({"requires_faceted_answer": False}),
            ],
            {
                "category": "schema_validation",
                "pydantic_path": ("interpreted_question",),
                "pydantic_type": "missing",
            },
        ),
    ],
)
def test_argo_planner_exposes_a_non_sensitive_protocol_diagnostic(
    responses: list[GenerationResponse],
    expected_diagnostic: dict[str, object],
) -> None:
    client = _SequencePlanningClient(responses)
    question = "Question confidentielle à ne jamais exposer"

    with pytest.raises(ArgoProtocolError) as error:
        ArgoQueryPlanningService(client).plan(question)

    diagnostic = error.value.diagnostic
    assert isinstance(diagnostic, QueryPlanningProtocolDiagnostic)
    assert diagnostic.model_dump() == expected_diagnostic
    assert question not in str(diagnostic)


def test_deterministic_plan_is_only_marked_as_fallback() -> None:
    result = deterministic_query_plan(
        "Impact de l'élevage en barrique sur les arômes et la structure du Calvados ?"
    )

    assert result.used_fallback is True
    assert result.prompt_tokens == 0


def test_deterministic_plan_expands_apple_juice_protein_stability_storage() -> None:
    result = deterministic_query_plan(
        "Comment la température et la durée de stockage modifient-elles la "
        "stabilité protéique du jus de pomme ?"
    )

    assert result.used_fallback is True
    assert result.plan.process_terms_en[:3] == [
        "storage",
        "storage temperature",
        "storage time",
    ]
    assert [axis.key for axis in result.plan.axes] == ["protein_stability"]
    assert any(
        "protein stability" in query and "storage temperature" in query
        for query in result.plan.retrieval_queries
    )


def test_deterministic_plan_expands_fining_and_plant_animal_comparison() -> None:
    result = deterministic_query_plan(
        "Quel est l'intérêt du collage dans les jus de pomme ? Donner des éléments "
        "de comparaison entre les colles végétales et animales."
    )

    assert "fining" in result.plan.process_terms_en
    assert [axis.key for axis in result.plan.axes] == [
        "fining_effects",
        "fining_agents_comparison",
        "fining_mechanisms_conditions",
    ]
    assert any("bentonite" in query for query in result.plan.retrieval_queries)
    assert result.plan.requires_faceted_answer is True
    assert any(
        "fining" in query and "plant proteins" in query and "gelatin" in query
        for query in result.plan.retrieval_queries
    )


def test_plan_propagates_excluded_concepts_to_scientific_intent() -> None:
    plan = ResearchQueryPlan.model_validate(
        {
            "interpreted_question": "Effect of a process on an exact matrix",
            "requires_faceted_answer": False,
            "excluded_concepts": ["similar process", "unrelated matrix"],
            "axes": [
                {
                    "key": "overall",
                    "label": "Scientific question",
                    "question": "Effect of the process on the matrix",
                    "terms_fr": ["procede"],
                    "terms_en": ["process"],
                    "search_queries": ["exact process matrix", "process exact matrix"],
                }
            ],
            "retrieval_queries": ["exact process matrix"],
        }
    )

    intent = plan.scientific_intent("Effect of a process on an exact matrix")

    assert intent.excluded_terms[:2] == ["similar process", "unrelated matrix"]


def test_deep_plan_intent_completes_missing_deterministic_comparative_facets() -> None:
    plan = ResearchQueryPlan.model_validate(
        {
            "interpreted_question": "Comparer des colles végétales et animales",
            "axes": [
                {
                    "key": "fining_effects",
                    "label": "Effets du collage",
                    "question": "Quels sont les effets du collage ?",
                    "terms_fr": ["collage"],
                    "terms_en": ["fining"],
                    "search_queries": ["apple juice fining effects"],
                }
            ],
            "retrieval_queries": ["apple juice fining effects"],
        }
    )

    intent = plan.scientific_intent(
        "Comparer les colles végétales et animales dans le jus de pomme.", deep=True
    )

    assert [facet.key for facet in intent.facets] == [
        "fining_effects",
        "fining_agents_comparison",
        "fining_mechanisms_conditions",
        "fining_tradeoffs_limits",
    ]
