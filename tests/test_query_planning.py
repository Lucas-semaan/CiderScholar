from __future__ import annotations

import json

from app.llm.contracts import GenerationMetrics, GenerationResponse
from app.retrieval.query_planning import (
    ArgoQueryPlanningService,
    ResearchQueryPlan,
    deterministic_query_plan,
)


def _response(content: dict[str, object]) -> GenerationResponse:
    return GenerationResponse(
        model="planner-test",
        content=json.dumps(content),
        done_reason="stop",
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
    prompt = client.messages[0]["content"]
    assert "concept_definition" in prompt
    assert "faux amis" in prompt
    assert "excluded_concepts" in prompt
    assert "La décomposition n'est jamais systématique" in client.messages[0]["content"]


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
    assert [axis.key for axis in result.plan.axes] == ["aroma", "structure"]
    assert "apple brandy oak aging volatile compounds esters" in result.plan.retrieval_queries
    assert (
        "cider brandy wood aging phenolics tannins acidity color mouthfeel"
        in result.plan.retrieval_queries
    )
    assert intent.matrix_close[:2] == ["apple brandy", "apple spirit"]
    assert [facet.key for facet in intent.facets] == ["aroma", "structure"]
    assert "phenolic compounds" in intent.facet("structure").terms_en


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
