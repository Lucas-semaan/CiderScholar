from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.expert_feedback.models import (
    CandidatePatch,
    ChunkReference,
    Diagnosis,
    ExpertCorrectionCreate,
    ImprovementBudget,
)
from app.knowledge.contracts import content_hash, portable_relative_path
from app.knowledge.models import KNOWLEDGE_ITEM_ADAPTER, KnowledgePackage, RecipeData


def test_package_hash_is_order_independent_and_changes_with_content(expert_package_payload) -> None:
    payload = expert_package_payload
    second = deepcopy(payload["items"][0])
    second["id"] = "taxonomy.other"
    payload["items"].append(second)
    package = KnowledgePackage.model_validate(payload)
    payload["items"].reverse()
    assert KnowledgePackage.model_validate(payload).package_sha256 == package.package_sha256
    payload["items"][0]["body"] = "Changed instruction."
    assert KnowledgePackage.model_validate(payload).package_sha256 != package.package_sha256
    assert package.items[0].content_sha256 == content_hash(package.items[0].model_dump(mode="json"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("revision", True),
        ("approval", "approved"),
        ("referenced_by", []),
        ("content_sha256", "a" * 64),
    ],
)
def test_items_refuse_untrusted_metadata(expert_package_payload, field, value) -> None:
    item = expert_package_payload["items"][0]
    item[field] = value
    with pytest.raises(ValidationError):
        KNOWLEDGE_ITEM_ADAPTER.validate_python(item)


def test_nested_items_and_collections_are_immutable(expert_package_payload) -> None:
    package = KnowledgePackage.model_validate(expert_package_payload)
    with pytest.raises(ValidationError, match="frozen"):
        package.items[0].data.canonical_term = "Other"
    assert isinstance(package.items, tuple)
    assert isinstance(package.items[0].data.aliases_fr, tuple)


def test_only_method_policies_can_be_global_requirements(expert_package_payload) -> None:
    item = expert_package_payload["items"][0]
    item.update(authority="accepted_user_method", required=True)
    with pytest.raises(ValidationError, match="required items must be method policies"):
        KNOWLEDGE_ITEM_ADAPTER.validate_python(item)


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "docs/../outside.md",
        "/absolute.md",
        "C:/outside.md",
        "C:outside.md",
        "docs\\file.md",
        "//host/file.md",
        "docs/NUL.md",
        "docs/file.md ",
        "docs/./file.md",
    ],
)
def test_provenance_paths_are_portable_and_contained(path) -> None:
    with pytest.raises(ValueError):
        portable_relative_path(path)
    assert portable_relative_path("docs/METHOD.md") == "docs/METHOD.md"


def test_duplicate_item_identity_is_not_silently_overwritten(expert_package_payload) -> None:
    expert_package_payload["items"].append(deepcopy(expert_package_payload["items"][0]))
    with pytest.raises(ValidationError, match="duplicate item IDs"):
        KnowledgePackage.model_validate(expert_package_payload)


def test_route_cannot_hide_operational_dependencies(expert_package_payload) -> None:
    item = expert_package_payload["items"][0]
    item.update(
        kind="route",
        stage="routing",
        data={"match": {"any_terms": ["synthetic"]}, "target_ids": ["taxonomy.synthetic"]},
    )
    with pytest.raises(ValidationError, match="declared dependencies"):
        KNOWLEDGE_ITEM_ADAPTER.validate_python(item)


def test_recipe_rejects_missing_validation_and_extra_retrieval() -> None:
    step = {
        "id": "retrieve",
        "handler": "retrieve_grouped",
        "knowledge_ids": [],
        "input_contract": "hypothesis_plan",
        "output_contract": "sqlite_candidates",
    }
    for steps in ([step], [step, {**step, "id": "second"}]):
        with pytest.raises(ValidationError, match="execution graph"):
            RecipeData(recipe_version="1.0.0", steps=steps)
    with pytest.raises(ValidationError):
        RecipeData(recipe_version="1.0.0", steps=[{**step, "handler": "shell"}])


def test_recipe_contracts_cannot_relabel_hypothesis_as_evidence() -> None:
    with pytest.raises(ValidationError, match="contracts must match"):
        RecipeData(
            recipe_version="1.0.0",
            interaction_mode="conversation",
            steps=[
                {
                    "id": "synthesize",
                    "handler": "synthesize_validated",
                    "input_contract": "hypothesis_plan",
                    "output_contract": "validated_answer",
                }
            ],
        )


def test_corrections_are_proposals_not_approvals() -> None:
    payload = {
        "client_request_id": uuid4(),
        "problem": "Synthetic problem",
        "proposed_correction": "Synthetic correction",
        "scope": "reusable_method",
    }
    correction = ExpertCorrectionCreate.model_validate(payload)
    assert correction.evidence_refs == ()
    for extra in ({"state": "approved"}, {"authority": "admin"}, {"claim_id": "claim-1"}):
        with pytest.raises(ValidationError):
            ExpertCorrectionCreate.model_validate({**payload, **extra})
    assert ImprovementBudget().model_dump() == {
        "diagnosis": 0,
        "compilation": 0,
        "review": 0,
        "evaluation": 0,
    }


def test_evidence_references_require_original_source_identity_and_pages() -> None:
    payload = {
        "kind": "chunk",
        "corpus_id": "synthetic",
        "article_id": "synthetic-article",
        "chunk_id": 1,
        "page_start": 3,
        "page_end": 2,
        "content_sha256": "a" * 64,
    }
    with pytest.raises(ValidationError, match="page_end"):
        ChunkReference.model_validate(payload)
    payload["page_end"] = 3
    assert ChunkReference.model_validate(payload).chunk_id == 1


@pytest.mark.parametrize(
    "cause",
    [
        "corpus_gap",
        "index_gap",
        "validator_bug",
        "expert_ambiguity",
        "runtime_failure",
        "insufficient_trace",
        "source_changed",
    ],
)
def test_non_method_diagnoses_cannot_authorize_knowledge_patches(cause) -> None:
    with pytest.raises(ValidationError, match="supported, traceable"):
        Diagnosis(
            correction_id=uuid4(),
            correction_revision=1,
            primary_cause=cause,
            rationale="Synthetic diagnosis",
            confidence="supported",
            target_item_ids=["taxonomy.synthetic"],
            observed_ids=["synthetic"],
            verified_hashes=["a" * 64],
            proposed_action="knowledge_candidate",
        )


@pytest.mark.parametrize("cause", ["source_changed", "insufficient_trace", "expert_ambiguity"])
def test_blocking_contributing_causes_prevent_knowledge_patches(cause) -> None:
    with pytest.raises(ValidationError, match="supported, traceable"):
        Diagnosis(
            correction_id=uuid4(),
            correction_revision=1,
            primary_cause="knowledge_gap",
            contributing_causes=[cause],
            rationale="Synthetic diagnosis",
            confidence="supported",
            target_item_ids=["taxonomy.synthetic"],
            observed_ids=["synthetic"],
            verified_hashes=["a" * 64],
            proposed_action="knowledge_candidate",
        )


def test_patch_cannot_self_approve_or_replace_without_expected_hash(expert_package_payload) -> None:
    item = expert_package_payload["items"][0]
    payload = {
        "base_release_id": uuid4(),
        "diagnosis_sha256": "a" * 64,
        "operations": [
            {
                "operation": "add_item",
                "item_id": item["id"],
                "item": item,
                "justification": "Synthetic proposed edit",
            }
        ],
    }
    assert CandidatePatch.model_validate(payload).operations[0].item.authority == "proposal"
    item["authority"] = "reviewed_method"
    with pytest.raises(ValidationError, match="only propose"):
        CandidatePatch.model_validate(payload)
    item["authority"] = "proposal"
    payload["operations"][0]["operation"] = "replace_item"
    with pytest.raises(ValidationError, match="expected hash"):
        CandidatePatch.model_validate(payload)
