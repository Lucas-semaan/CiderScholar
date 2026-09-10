from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.knowledge.graph import KnowledgeGraph
from app.knowledge.models import KnowledgePackage
from app.knowledge.validation import validate_package


def test_validation_rejects_boolean_revision_in_manufactured_model(expert_package_payload) -> None:
    package = KnowledgePackage.model_validate(expert_package_payload)
    item = package.items[0].model_copy(update={"revision": True})
    manufactured = package.model_copy(update={"items": (item,)})
    with pytest.raises(ValidationError):
        validate_package(manufactured)


def test_graph_finds_missing_dependencies(expert_package_payload) -> None:
    expert_package_payload["items"][0]["depends_on"] = ["taxonomy.absent"]
    report = validate_package(KnowledgePackage.model_validate(expert_package_payload))
    assert not report.structurally_valid
    assert report.issues[0].code == "missing_dependency"


def test_cycles_fail_without_confusing_reverse_links_with_dependencies(
    expert_package_payload,
) -> None:
    first = expert_package_payload["items"][0]
    second = {**deepcopy(first), "id": "taxonomy.second", "depends_on": [first["id"]]}
    first["depends_on"] = [second["id"]]
    expert_package_payload["items"].append(second)
    assert {
        issue.code
        for issue in validate_package(
            KnowledgePackage.model_validate(expert_package_payload)
        ).issues
    } == {"dependency_cycle"}
    first["depends_on"] = []
    package = KnowledgePackage.model_validate(expert_package_payload)
    graph = KnowledgeGraph.build(package)
    assert graph.referenced_by(first["id"]) == (second["id"],)
    assert graph.closure((second["id"],)) == tuple(sorted((first["id"], second["id"])))
    assert graph.affected_by((first["id"],)) == graph.closure((second["id"],))
    assert graph.affected_by((second["id"],)) == (second["id"],)


def test_a_reviewed_rule_cannot_depend_on_an_unreviewed_proposal(expert_package_payload) -> None:
    first = expert_package_payload["items"][0]
    second = {
        **deepcopy(first),
        "id": "taxonomy.reviewed",
        "authority": "reviewed_method",
        "depends_on": [first["id"]],
    }
    expert_package_payload["items"].append(second)
    report = validate_package(KnowledgePackage.model_validate(expert_package_payload))
    assert report.issues[0].code == "unreviewed_dependency"
