"""Deterministic preview of expert instruction selection, with no retrieval or LLM calls."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Annotated, Literal

from pydantic import Field

from app.config import ExpertMemoryConfig
from app.knowledge.contracts import (
    ImmutableModel,
    ItemId,
    PositiveInt,
    Sha256,
    ShortText,
    canonical_json,
)
from app.knowledge.graph import KnowledgeGraph
from app.knowledge.models import (
    GatewayItem,
    KnowledgeItem,
    KnowledgePackage,
    MatchCriteria,
    RouteItem,
)
from app.knowledge.validation import validate_package


class SelectedItem(ImmutableModel):
    id: ItemId
    revision: PositiveInt
    content_sha256: Sha256


class RouteDecision(ImmutableModel):
    route_id: ItemId
    reason: Literal[
        "selected", "no_match", "excluded", "gateway_uncertain", "unreviewed", "budget_exceeded"
    ]


class StageContext(ImmutableModel):
    stage: Literal["planning", "semantic_filter", "generation"]
    payload: str
    characters: int = Field(ge=0)


class RoutingDecision(ImmutableModel):
    schema_version: Literal[1] = 1
    preview_only: Literal[True] = True
    scientific_approval: Literal[False] = False
    include_proposals: bool = Field(default=False, strict=True)
    package_sha256: Sha256
    question_sha256: Sha256
    selected: tuple[SelectedItem, ...]
    routes: tuple[RouteDecision, ...]
    contexts: tuple[StageContext, ...]
    fallback: Literal["none", "off", "budget_fallback"] = "none"
    ambiguity_ids: tuple[ItemId, ...] = ()


class _RoutingInput(ImmutableModel):
    question: str = Field(min_length=1, max_length=4000)
    language: Literal["fr", "en"] | None = None
    facet_ids: Annotated[tuple[ShortText, ...], Field(max_length=20)] = ()
    include_proposals: bool = Field(default=False, strict=True)


def normalized_tokens(value: str) -> tuple[str, ...]:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return tuple(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def contains_term(tokens: tuple[str, ...], term: str) -> bool:
    expected = normalized_tokens(term)
    return bool(expected) and any(
        tokens[index : index + len(expected)] == expected
        for index in range(len(tokens) - len(expected) + 1)
    )


def _matches(
    criteria: MatchCriteria,
    tokens: tuple[str, ...],
    facet_ids: frozenset[str],
    language: str | None,
) -> bool:
    if not criteria.constrained:
        return False
    return (
        all(contains_term(tokens, term) for term in criteria.all_terms)
        and (
            not criteria.any_terms
            or any(contains_term(tokens, term) for term in criteria.any_terms)
        )
        and set(criteria.facet_ids) <= facet_ids
        and (criteria.language is None or criteria.language == language)
    )


def _contexts(items: dict[str, KnowledgeItem], selected: set[str]) -> tuple[StageContext, ...]:
    contexts = []
    for stage in ("planning", "semantic_filter", "generation"):
        contents = [
            {
                "id": item.id,
                "kind": item.kind,
                "data": item.data.model_dump(mode="json"),
                "body": item.body,
            }
            for item_id in sorted(selected)
            if (item := items[item_id]).stage == stage
        ]
        payload = canonical_json(contents) if contents else ""
        contexts.append(StageContext(stage=stage, payload=payload, characters=len(payload)))
    return tuple(contexts)


def _fits(items: dict[str, KnowledgeItem], selected: set[str], config: ExpertMemoryConfig) -> bool:
    if len(selected) > config.max_selected_items:
        return False
    limits = {
        "planning": config.planning_max_characters,
        "semantic_filter": config.semantic_max_characters,
        "generation": config.generation_max_characters,
    }
    return all(
        context.characters <= limits[context.stage] for context in _contexts(items, selected)
    )


def preview_routing(
    question: str,
    package: KnowledgePackage,
    *,
    config: ExpertMemoryConfig | None = None,
    language: Literal["fr", "en"] | None = None,
    facet_ids: tuple[str, ...] = (),
    include_proposals: bool = False,
) -> RoutingDecision:
    """Preview only. A package hash or authority declaration is not an activation credential."""

    request = _RoutingInput(
        question=question,
        language=language,
        facet_ids=facet_ids,
        include_proposals=include_proposals,
    )
    if not request.question.strip():
        raise ValueError("question must contain 1 to 4000 characters")
    package = KnowledgePackage.model_validate(package.model_dump(mode="python", warnings=False))
    if not validate_package(package).structurally_valid:
        raise ValueError("cannot route an invalid package")
    config = ExpertMemoryConfig.model_validate(
        (config or ExpertMemoryConfig(mode="shadow")).model_dump(mode="python", warnings=False)
    )
    if config.mode == "active":
        raise ValueError("preview routing accepts only off or shadow, never activation")
    common = {
        "package_sha256": package.package_sha256,
        "question_sha256": hashlib.sha256(request.question.encode("utf-8")).hexdigest(),
        "include_proposals": request.include_proposals,
    }
    items = {item.id: item for item in package.items}
    if config.mode == "off":
        return RoutingDecision(
            **common, selected=(), routes=(), contexts=_contexts(items, set()), fallback="off"
        )
    graph = KnowledgeGraph.build(package)
    required = tuple(
        item.id for item in package.items if item.required and item.authority != "proposal"
    )
    # A global requirement can only be a method policy. Specialised vocabulary,
    # routes and gateways must still satisfy their input characteristics.
    if any(items[item_id].kind != "method_policy" for item_id in required):
        raise ValueError("required items must be method policies")
    selected = set(graph.closure(required))
    if not _fits(items, selected, config):
        return RoutingDecision(
            **common,
            selected=(),
            routes=(),
            contexts=_contexts(items, set()),
            fallback="budget_fallback",
        )
    decisions: list[RouteDecision] = []
    ambiguities: set[str] = set()
    tokens = normalized_tokens(request.question)
    facets = frozenset(request.facet_ids)
    routes = sorted(
        (item for item in package.items if isinstance(item, RouteItem)),
        key=lambda item: (-item.data.priority, item.id),
    )
    for route in routes:
        reason: Literal[
            "selected", "no_match", "excluded", "gateway_uncertain", "unreviewed", "budget_exceeded"
        ] = "selected"
        group = set(graph.closure((route.id,)))
        if not request.include_proposals and any(
            items[item_id].authority == "proposal" for item_id in group
        ):
            reason = "unreviewed"
        elif not _matches(route.data.match, tokens, facets, request.language):
            reason = "no_match"
        elif _matches(route.data.exclude, tokens, facets, request.language):
            reason = "excluded"
        else:
            failed_gateways = [
                items[item_id]
                for item_id in sorted(group)
                if isinstance(items[item_id], GatewayItem)
                and not _matches(items[item_id].data.criteria, tokens, facets, request.language)
            ]
            if failed_gateways:
                reason = "gateway_uncertain"
                ambiguities.update(
                    gateway.id
                    for gateway in failed_gateways
                    if gateway.data.on_uncertain == "record_ambiguity"
                )
            elif not _fits(items, selected | group, config):
                reason = "budget_exceeded"
        if reason == "selected":
            selected.update(group)
        decisions.append(RouteDecision(route_id=route.id, reason=reason))
    return RoutingDecision(
        **common,
        selected=tuple(
            SelectedItem(
                id=items[item_id].id,
                revision=items[item_id].revision,
                content_sha256=items[item_id].content_sha256,
            )
            for item_id in sorted(selected)
        ),
        routes=tuple(decisions),
        contexts=_contexts(items, selected),
        ambiguity_ids=tuple(sorted(ambiguities)),
    )
