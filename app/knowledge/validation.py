"""Structural package checks; passing these is never scientific approval."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.knowledge.contracts import ImmutableModel, canonical_json
from app.knowledge.graph import KnowledgeGraph, KnowledgeGraphError
from app.knowledge.models import KnowledgePackage, RecipeItem, RouteItem

MAX_PACKAGE_BYTES = 2 * 1024 * 1024


class KnowledgeIssue(ImmutableModel):
    code: str
    file: str | None = None
    item_id: str | None = None
    field: str | None = None


class KnowledgeValidationReport(ImmutableModel):
    schema_version: Literal[1] = 1
    structurally_valid: bool
    scientific_approval: Literal[False] = False
    package_sha256: str | None = None
    item_count: int = Field(default=0, ge=0)
    issues: tuple[KnowledgeIssue, ...] = ()
    dependencies: tuple[tuple[str, tuple[str, ...]], ...] = ()
    referenced_by: tuple[tuple[str, tuple[str, ...]], ...] = ()


ALLOWED_DEPENDENCY_KINDS = {
    "taxonomy": {"taxonomy"},
    "method_policy": {"taxonomy", "method_policy"},
    "gateway": {"taxonomy"},
    "route": {"taxonomy", "method_policy", "gateway", "recipe"},
    "recipe": {"taxonomy", "method_policy"},
}
HANDLER_STAGES = {
    "plan_hypothesis": "planning",
    "retrieve_grouped": None,
    "select_global_evidence": "semantic_filter",
    "synthesize_validated": "generation",
}


def validate_package(package: KnowledgePackage) -> KnowledgeValidationReport:
    # Re-validate even an object manufactured with model_construct/model_copy.
    package = KnowledgePackage.model_validate(package.model_dump(mode="python"))
    issues: list[KnowledgeIssue] = []
    if len(canonical_json(package.model_dump(mode="json")).encode("utf-8")) > MAX_PACKAGE_BYTES:
        issues.append(KnowledgeIssue(code="package_too_large"))
    try:
        graph = KnowledgeGraph.build(package)
    except KnowledgeGraphError as error:
        issues.extend(
            KnowledgeIssue(code=error.code, item_id=item_id) for item_id in error.item_ids
        )
        graph = None
    items = {item.id: item for item in package.items}
    for item in package.items:
        for dependency in item.depends_on:
            target = items.get(dependency)
            if target is not None and target.kind not in ALLOWED_DEPENDENCY_KINDS[item.kind]:
                issues.append(
                    KnowledgeIssue(
                        code="invalid_dependency_kind", item_id=item.id, field="depends_on"
                    )
                )
            if (
                target is not None
                and item.authority != "proposal"
                and target.authority == "proposal"
            ):
                issues.append(
                    KnowledgeIssue(
                        code="unreviewed_dependency", item_id=item.id, field="depends_on"
                    )
                )
        if isinstance(item, RouteItem):
            for item_id in item.data.target_ids:
                if item_id in items and items[item_id].kind == "gateway":
                    issues.append(
                        KnowledgeIssue(
                            code="gateway_is_not_instruction",
                            item_id=item.id,
                            field="data.target_ids",
                        )
                    )
        if isinstance(item, RecipeItem):
            for step in item.data.steps:
                for item_id in step.knowledge_ids:
                    if item_id in items and items[item_id].stage != HANDLER_STAGES[step.handler]:
                        issues.append(
                            KnowledgeIssue(
                                code="wrong_recipe_stage", item_id=item.id, field="data.steps"
                            )
                        )
    return KnowledgeValidationReport(
        structurally_valid=not issues,
        package_sha256=package.package_sha256,
        item_count=len(package.items),
        issues=tuple(
            sorted(issues, key=lambda issue: (issue.code, issue.item_id or "", issue.field or ""))
        ),
        dependencies=graph.dependencies if graph else (),
        referenced_by=tuple((item.id, graph.referenced_by(item.id)) for item in package.items)
        if graph
        else (),
    )
