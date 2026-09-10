from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.config import ExpertMemoryConfig
from app.knowledge.loader import lint_package, load_package
from app.knowledge.models import KnowledgePackage, RecipeItem
from app.knowledge.routing import RoutingDecision, preview_routing

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_POLICIES = {
    "policy.hypothesis_scope",
    "policy.relevance_grades",
    "policy.sqlite_evidence",
}
PROPOSED_ROUTES = {"route.malolactic", "route.cuvage", "route.matrix_transfer"}


@pytest.fixture(scope="module")
def method_seed() -> KnowledgePackage:
    return load_package(ROOT / "knowledge", source_root=ROOT)


def selected_ids(decision: RoutingDecision) -> set[str]:
    return {item.id for item in decision.selected}


def route_reasons(decision: RoutingDecision) -> dict[str, str]:
    return {route.route_id: route.reason for route in decision.routes}


def test_seed_has_verified_sources_without_claiming_scientific_approval(method_seed) -> None:
    report = lint_package(ROOT / "knowledge", source_root=ROOT)
    assert report.structurally_valid
    assert report.scientific_approval is False
    assert report.item_count == 13
    assert report.package_sha256 == method_seed.package_sha256
    for item in method_seed.items:
        for source in item.provenance:
            assert (
                source.revision_sha256
                == hashlib.sha256((ROOT / source.document).read_bytes()).hexdigest()
            )


def test_seed_preserves_the_boundary_between_existing_method_and_proposals(method_seed) -> None:
    accepted = [item for item in method_seed.items if item.authority == "accepted_user_method"]
    proposals = [item for item in method_seed.items if item.authority == "proposal"]
    assert len(accepted) == 5
    assert all(item.kind == "method_policy" for item in accepted)
    assert {item.id for item in accepted if item.required} == REQUIRED_POLICIES
    assert len(proposals) == 8
    assert all(not item.required for item in proposals)
    assert {item.id for item in proposals if item.kind == "route"} == PROPOSED_ROUTES
    assert not any(item.authority == "reviewed_method" for item in method_seed.items)


def test_seed_does_not_select_proposed_routes_without_explicit_preview_opt_in(method_seed) -> None:
    decision = preview_routing(
        "Comparer FML et cuvage du cidre puis transposer les résultats d'une autre matrice.",
        method_seed,
    )
    assert selected_ids(decision) == REQUIRED_POLICIES
    assert route_reasons(decision) == dict.fromkeys(PROPOSED_ROUTES, "unreviewed")
    assert decision.include_proposals is False
    assert decision.preview_only is True
    assert decision.scientific_approval is False


@pytest.mark.parametrize(
    "question,language,route_id,taxonomy_id",
    [
        (
            "Quels facteurs influencent la FML du cidre ?",
            "fr",
            "route.malolactic",
            "taxonomy.malolactic_fermentation",
        ),
        (
            "La TML dans la pomme destinée au cidre",
            "fr",
            "route.malolactic",
            "taxonomy.malolactic_fermentation",
        ),
        (
            "How does MLF affect cider?",
            "en",
            "route.malolactic",
            "taxonomy.malolactic_fermentation",
        ),
        (
            "Malolactic fermentation in apple cider",
            "en",
            "route.malolactic",
            "taxonomy.malolactic_fermentation",
        ),
        ("Quels effets du cuvage de pomme ?", "fr", "route.cuvage", "taxonomy.cuvage"),
        (
            "Macération de la pulpe de pommes avant pressurage",
            "fr",
            "route.cuvage",
            "taxonomy.cuvage",
        ),
        ("Apple mash maceration before pressing", "en", "route.cuvage", "taxonomy.cuvage"),
        ("Pre-press maceration for cider", "en", "route.cuvage", "taxonomy.cuvage"),
    ],
)
def test_seed_preview_routes_domain_terms_in_french_and_english(
    method_seed, question, language, route_id, taxonomy_id
) -> None:
    decision = preview_routing(question, method_seed, language=language, include_proposals=True)
    assert route_reasons(decision)[route_id] == "selected"
    assert {route_id, taxonomy_id, "gateway.cider_domain", "recipe.chat_research"} <= selected_ids(
        decision
    )
    assert selected_ids(decision) >= REQUIRED_POLICIES
    assert decision.preview_only is True
    assert decision.scientific_approval is False


@pytest.mark.parametrize(
    "question,route_id",
    [
        ("FMLase dans le cidre", "route.malolactic"),
        ("Les récepteurs MLF2 dans la pomme", "route.malolactic"),
        ("Décuvage du cidre", "route.cuvage"),
        ("Stockage du jus de pomme", "route.cuvage"),
        ("Chauffage de la pulpe de pomme", "route.cuvage"),
        ("Transport des pommes", "route.cuvage"),
    ],
)
def test_seed_does_not_infer_process_from_neighbouring_terms(
    method_seed, question, route_id
) -> None:
    decision = preview_routing(question, method_seed, include_proposals=True)
    assert route_reasons(decision)[route_id] == "no_match"
    assert selected_ids(decision) == REQUIRED_POLICIES


@pytest.mark.parametrize(
    "question,route_id",
    [
        ("Comment expliquer la FML ?", "route.malolactic"),
        ("What factors affect MLF in wine?", "route.malolactic"),
        ("Quels effets du cuvage sur le vin ?", "route.cuvage"),
        ("Can results from another matrix transfer to wine?", "route.matrix_transfer"),
    ],
)
def test_seed_reports_ambiguous_domain_without_injecting_specialised_terms(
    method_seed, question, route_id
) -> None:
    decision = preview_routing(question, method_seed, include_proposals=True)
    assert route_reasons(decision)[route_id] == "gateway_uncertain"
    assert decision.ambiguity_ids == ("gateway.cider_domain",)
    assert selected_ids(decision) == REQUIRED_POLICIES


@pytest.mark.parametrize(
    "question",
    [
        "Comparer le cuvage de pomme à la macération carbonique.",
        "Compare apple mash maceration and carbonic maceration.",
    ],
)
def test_seed_preview_preserves_explicit_cuvage_exclusions(method_seed, question) -> None:
    decision = preview_routing(question, method_seed, include_proposals=True)
    assert route_reasons(decision)["route.cuvage"] == "excluded"
    assert selected_ids(decision) == REQUIRED_POLICIES


@pytest.mark.parametrize(
    "question",
    [
        "Peut-on transposer les résultats d'une autre matrice au cidre ?",
        "Can results from another matrix transfer to apple cider?",
    ],
)
def test_seed_matrix_transfer_selects_the_indirect_evidence_policy(method_seed, question) -> None:
    decision = preview_routing(question, method_seed, include_proposals=True)
    assert route_reasons(decision)["route.matrix_transfer"] == "selected"
    semantic = next(context for context in decision.contexts if context.stage == "semantic_filter")
    assert "policy.matrix_transfer" in {item["id"] for item in json.loads(semantic.payload)}


def test_combined_seed_routes_fit_default_budgets_without_selecting_conversation_recipe(
    method_seed,
) -> None:
    original_authorities = {item.id: item.authority for item in method_seed.items}
    decision = preview_routing(
        "Comparer FML et cuvage du cidre puis transposer les résultats d'une autre matrice.",
        method_seed,
        include_proposals=True,
    )
    assert route_reasons(decision) == dict.fromkeys(PROPOSED_ROUTES, "selected")
    assert len(decision.selected) == 12
    assert "recipe.chat_conversation" not in selected_ids(decision)
    assert {item.id: item.authority for item in method_seed.items} == original_authorities
    limits = {"planning": 2000, "semantic_filter": 1600, "generation": 1600}
    for context in decision.contexts:
        assert context.characters == len(context.payload)
        assert context.characters <= limits[context.stage]


def test_seed_budget_refuses_the_whole_lower_priority_route(method_seed) -> None:
    decision = preview_routing(
        "Comparer FML et cuvage du cidre puis transposer les résultats d'une autre matrice.",
        method_seed,
        config=ExpertMemoryConfig(mode="shadow", max_selected_items=11),
        include_proposals=True,
    )
    assert route_reasons(decision) == {
        "route.malolactic": "selected",
        "route.cuvage": "selected",
        "route.matrix_transfer": "budget_exceeded",
    }
    assert len(decision.selected) == 10
    assert "policy.matrix_transfer" not in selected_ids(decision)


def test_seed_research_recipe_keeps_one_grouped_wave_and_sqlite_only_synthesis(method_seed) -> None:
    recipe = next(item for item in method_seed.items if item.id == "recipe.chat_research")
    assert isinstance(recipe, RecipeItem)
    assert recipe.data.interaction_mode == "research"
    assert [step.handler for step in recipe.data.steps] == [
        "plan_hypothesis",
        "retrieve_grouped",
        "select_global_evidence",
        "synthesize_validated",
    ]
    assert recipe.data.steps[-1].input_contract == "sqlite_evidence"
    assert "policy.hypothesis_scope" not in recipe.data.steps[-1].knowledge_ids
    assert "policy.sqlite_evidence" in recipe.data.steps[-1].knowledge_ids
