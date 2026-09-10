from __future__ import annotations

import json
import socket
from copy import deepcopy
from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.config import ExpertMemoryConfig
from app.knowledge.contracts import canonical_json
from app.knowledge.models import KnowledgePackage
from app.knowledge.routing import contains_term, normalized_tokens, preview_routing
from scripts.preview_expert_routing import main


@pytest.fixture
def routing_payload(expert_package_payload):
    payload = expert_package_payload
    item = payload["items"][0]
    item["authority"] = "reviewed_method"
    route = {
        **deepcopy(item),
        "id": "route.synthetic",
        "kind": "route",
        "stage": "routing",
        "depends_on": [item["id"]],
        "data": {
            "priority": 10,
            "match": {"any_terms": ["procédé fictif", "SPR"]},
            "target_ids": [item["id"]],
        },
    }
    payload["items"].append(route)
    return payload


def _required_policy(payload):
    policy = {
        **deepcopy(payload["items"][0]),
        "id": "method_policy.safety",
        "kind": "method_policy",
        "required": True,
        "stage": "generation",
        "data": {"instruction": "Synthetic safety method.", "applies_to": ["all answers"]},
    }
    payload["items"].append(policy)
    return policy


@pytest.mark.parametrize(
    "question,term,expected",
    [
        ("PROCÉDÉ-FICTIF", "procede fictif", True),
        ("fermentation d’essai", "FERMENTATION D'ESSAI", True),
        ("proce\u0301de\u0301 fictif", "procédé fictif", True),
        ("(SPR)", "spr", True),
        ("sprinter", "SPR", False),
        ("S P R", "SPR", False),
        ("fictif procédé", "procédé fictif", False),
        ("synthetic", "---", False),
    ],
)
def test_matching_uses_normalized_whole_token_sequences(question, term, expected) -> None:
    assert contains_term(normalized_tokens(question), term) is expected


def test_preview_is_offline_and_hashes_original_input(routing_payload, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("routing preview must not use the network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    package = KnowledgePackage.model_validate(routing_payload)
    question = "  Un procédé fictif ?  "
    result = preview_routing(question, package)
    assert result.preview_only and result.scientific_approval is False
    assert result.include_proposals is False
    assert result.question_sha256 == sha256(question.encode("utf-8")).hexdigest()
    assert result.package_sha256 == package.package_sha256
    assert {item.id: item.content_sha256 for item in result.selected} == {
        item.id: item.content_sha256 for item in package.items
    }


@pytest.mark.parametrize(
    "language,facets,reason",
    [
        ("fr", ("matrix.synthetic",), "selected"),
        ("en", ("matrix.synthetic",), "no_match"),
        (None, ("matrix.synthetic",), "no_match"),
        ("fr", (), "no_match"),
    ],
)
def test_matching_requires_terms_facets_and_language(routing_payload, language, facets, reason):
    routing_payload["items"][1]["data"]["match"] = {
        "all_terms": ["procédé", "fictif"],
        "any_terms": ["SPR", "essai"],
        "facet_ids": ["matrix.synthetic"],
        "language": "fr",
    }
    result = preview_routing(
        "procédé fictif, essai",
        KnowledgePackage.model_validate(routing_payload),
        language=language,
        facet_ids=facets,
    )
    assert result.routes[0].reason == reason


def test_exclusions_override_a_match_and_empty_exclusions_do_not(routing_payload):
    package = KnowledgePackage.model_validate(routing_payload)
    assert preview_routing("SPR interdit", package).routes[0].reason == "selected"
    routing_payload["items"][1]["data"]["exclude"] = {"any_terms": ["interdit"]}
    result = preview_routing("SPR interdit", KnowledgePackage.model_validate(routing_payload))
    assert result.routes[0].reason == "excluded"
    assert result.selected == ()


@pytest.mark.parametrize("on_uncertain", ["record_ambiguity", "keep_general_route"])
def test_uncertain_gateway_preserves_the_general_route(routing_payload, on_uncertain):
    specialised = routing_payload["items"][1]
    general = {**deepcopy(specialised), "id": "route.general"}
    general["data"]["priority"] = 0
    gateway = {
        **deepcopy(routing_payload["items"][0]),
        "id": "gateway.matrix",
        "kind": "gateway",
        "stage": "routing",
        "data": {"criteria": {"any_terms": ["matrice synthétique"]}, "on_uncertain": on_uncertain},
    }
    specialised["depends_on"].append(gateway["id"])
    routing_payload["items"].extend([general, gateway])
    package = KnowledgePackage.model_validate(routing_payload)
    result = preview_routing("SPR", package)
    assert [(decision.route_id, decision.reason) for decision in result.routes] == [
        ("route.synthetic", "gateway_uncertain"),
        ("route.general", "selected"),
    ]
    assert {item.id for item in result.selected} == {"route.general", "taxonomy.synthetic"}
    assert result.ambiguity_ids == (
        ("gateway.matrix",) if on_uncertain == "record_ambiguity" else ()
    )
    assert preview_routing("SPR matrice synthétique", package).routes[0].reason == "selected"


@pytest.mark.parametrize("proposal_target", [False, True])
def test_proposals_require_explicit_preview_permission(routing_payload, proposal_target):
    routing_payload["items"][1]["authority"] = "proposal"
    if proposal_target:
        routing_payload["items"][0]["authority"] = "proposal"
    package = KnowledgePackage.model_validate(routing_payload)
    original = package.model_dump()
    baseline = preview_routing("SPR", package)
    assert baseline.selected == ()
    assert baseline.routes[0].reason == "unreviewed"
    preview = preview_routing("SPR", package, include_proposals=True)
    assert len(preview.selected) == 2
    assert preview.include_proposals and preview.preview_only
    assert preview.scientific_approval is False
    assert package.model_dump() == original


def test_required_method_policies_are_global_and_fall_back_as_a_whole(routing_payload):
    policy = _required_policy(routing_payload)
    policy["depends_on"] = ["taxonomy.synthetic"]
    package = KnowledgePackage.model_validate(routing_payload)
    result = preview_routing("unrelated question", package)
    assert {item.id for item in result.selected} == {policy["id"], "taxonomy.synthetic"}
    result = preview_routing(
        "unrelated question",
        package,
        config=ExpertMemoryConfig(mode="shadow", generation_max_characters=0),
    )
    assert result.fallback == "budget_fallback"
    assert result.selected == ()
    assert all(context.payload == "" and context.characters == 0 for context in result.contexts)


def test_stage_budget_counts_exact_json_and_never_truncates_dependencies(routing_payload):
    routing_payload["items"][0]["body"] = 'Instruction synthétique : "contrôle"\n'
    package = KnowledgePackage.model_validate(routing_payload)
    baseline = preview_routing("SPR", package)
    context = next(context for context in baseline.contexts if context.stage == "planning")
    assert context.characters == len(context.payload)
    assert context.payload == canonical_json(json.loads(context.payload))
    exact = ExpertMemoryConfig(mode="shadow", planning_max_characters=context.characters)
    assert preview_routing("SPR", package, config=exact).selected == baseline.selected
    smaller = exact.model_copy(update={"planning_max_characters": context.characters - 1})
    result = preview_routing("SPR", package, config=smaller)
    assert result.routes[0].reason == "budget_exceeded"
    assert result.selected == ()
    assert all(context.payload == "" for context in result.contexts)


def test_shared_dependencies_count_once_and_order_is_deterministic(routing_payload):
    second = {**deepcopy(routing_payload["items"][1]), "id": "route.another"}
    routing_payload["items"].append(second)
    package = KnowledgePackage.model_validate(routing_payload)
    fits = ExpertMemoryConfig(mode="shadow", max_selected_items=3)
    baseline = preview_routing("SPR", package, config=fits)
    assert len(baseline.selected) == 3
    assert [route.route_id for route in baseline.routes] == ["route.another", "route.synthetic"]
    routing_payload["items"].reverse()
    assert (
        preview_routing("SPR", KnowledgePackage.model_validate(routing_payload), config=fits)
        == baseline
    )
    result = preview_routing(
        "SPR", package, config=ExpertMemoryConfig(mode="shadow", max_selected_items=2)
    )
    assert [route.reason for route in result.routes] == ["selected", "budget_exceeded"]
    assert {item.id for item in result.selected} == {"route.another", "taxonomy.synthetic"}


def test_a_large_priority_group_does_not_block_a_smaller_later_group(routing_payload):
    high = {**deepcopy(routing_payload["items"][1]), "id": "route.high"}
    high["data"]["priority"] = 100
    extra = {**deepcopy(routing_payload["items"][0]), "id": "taxonomy.extra"}
    high["depends_on"].append(extra["id"])
    routing_payload["items"].extend([high, extra])
    result = preview_routing(
        "SPR",
        KnowledgePackage.model_validate(routing_payload),
        config=ExpertMemoryConfig(mode="shadow", max_selected_items=2),
    )
    assert [route.reason for route in result.routes] == ["budget_exceeded", "selected"]
    assert {item.id for item in result.selected} == {"route.synthetic", "taxonomy.synthetic"}


def test_off_mode_has_no_selection_and_active_mode_is_refused(routing_payload):
    package = KnowledgePackage.model_validate(routing_payload)
    result = preview_routing(
        "SPR", package, config=ExpertMemoryConfig(mode="off"), include_proposals=True
    )
    assert result.fallback == "off" and result.include_proposals
    assert result.selected == result.routes == ()
    with pytest.raises(ValueError, match="never activation"):
        preview_routing("SPR", package, config=ExpertMemoryConfig(mode="active"))


@pytest.mark.parametrize("update", [{"max_selected_items": True}, {"mode": "unknown"}])
def test_config_is_revalidated_after_unchecked_copy(routing_payload, update):
    config = ExpertMemoryConfig(mode="shadow").model_copy(update=update)
    with pytest.raises(ValidationError):
        preview_routing("SPR", KnowledgePackage.model_validate(routing_payload), config=config)


def test_package_is_revalidated_without_converting_booleans_to_integers(routing_payload):
    package = KnowledgePackage.model_validate(routing_payload)
    invalid = package.items[0].model_copy(update={"revision": True})
    package = package.model_copy(update={"items": (invalid, package.items[1])})
    with pytest.raises(ValidationError):
        preview_routing("SPR", package)


def test_invalid_dependency_graph_cannot_be_previewed(routing_payload):
    routing_payload["items"][1]["depends_on"].append("taxonomy.missing")
    with pytest.raises(ValueError, match="invalid package"):
        preview_routing(
            "SPR", KnowledgePackage.model_validate(routing_payload), include_proposals=True
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"question": " "},
        {"question": "x" * 4001},
        {"language": "es"},
        {"facet_ids": "synthetic"},
        {"facet_ids": (123,)},
        {"facet_ids": (" ",)},
        {"facet_ids": tuple(str(index) for index in range(21))},
        {"include_proposals": "true"},
    ],
    ids=[
        "blank",
        "long",
        "language",
        "facet_string",
        "facet_number",
        "empty_facet",
        "many_facets",
        "proposal_string",
    ],
)
def test_inputs_are_closed_and_bounded(routing_payload, kwargs):
    with pytest.raises(ValueError):
        preview_routing(
            **{"question": "SPR", **kwargs},
            package=KnowledgePackage.model_validate(routing_payload),
        )


@pytest.mark.parametrize("include_proposals", [False, True])
def test_cli_redacts_question_and_context_and_reports_proposal_permission(
    routing_payload, monkeypatch, capsys, include_proposals
):
    for item in routing_payload["items"]:
        item["authority"] = "proposal"
        item["body"] = "private-instruction-marker"
    routing_payload["items"][1]["data"]["match"].update(
        language="fr", facet_ids=["matrix.synthetic"]
    )
    package = KnowledgePackage.model_validate(routing_payload)
    monkeypatch.setattr(
        "scripts.preview_expert_routing.load_package", lambda *args, **kwargs: package
    )
    args = [
        "--knowledge-dir",
        "synthetic",
        "--question",
        "SPR private-question-marker",
        "--language",
        "fr",
        "--facet-id",
        "matrix.synthetic",
    ]
    if include_proposals:
        args.append("--include-proposals")
    assert main(args) == 0
    output = capsys.readouterr().out
    assert "private-question-marker" not in output and "private-instruction-marker" not in output
    result = json.loads(output)
    assert result["preview_only"] and result["scientific_approval"] is False
    assert result["include_proposals"] is include_proposals
    assert result["routes"][0]["reason"] == ("selected" if include_proposals else "unreviewed")
    assert "contexts" not in result and "stage_characters" in result


def test_cli_error_is_structured_and_remains_only_a_preview(expert_knowledge_dir, capsys):
    assert (
        main(
            ["--knowledge-dir", str(expert_knowledge_dir), "--question", " ", "--include-proposals"]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "code": "invalid_routing_input",
        "preview_only": True,
        "scientific_approval": False,
        "include_proposals": True,
    }
