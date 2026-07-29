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
                "search_queries": ["cider\u200b fermentation temperature kinetics"],
            }
        ],
        "retrieval_queries": ["cider fermentation temperature kinetics"],
    }
    client = _FakePlanningClient(payload)

    result = ArgoQueryPlanningService(client).plan(
        "Quel est l'effet de la température sur la fermentation du cidre ?"
    )

    assert result.plan.requires_faceted_answer is False
    assert [axis.key for axis in result.plan.axes] == ["temperature"]
    assert result.plan.axes[0].search_queries == ["cider fermentation temperature kinetics"]
    assert result.prompt_tokens == 120
    assert client.options["json_schema"] == ResearchQueryPlan.model_json_schema()
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
                "search_queries": ["apple brandy oak aging volatile compounds esters"],
            },
            {
                "key": "structure",
                "label": "Structure en bouche",
                "question": "Quels changements structuraux sont dus au bois ?",
                "terms_fr": ["polyphénols", "tanins", "acidité", "couleur"],
                "terms_en": ["phenolics", "tannins", "acidity", "color", "mouthfeel"],
                "search_queries": [
                    "cider brandy wood aging phenolics tannins acidity color mouthfeel"
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
