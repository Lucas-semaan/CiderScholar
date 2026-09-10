"""Closed knowledge-file and package schemas. Parsing never approves a rule."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, TypeAdapter, field_validator, model_validator

from app.knowledge.contracts import (
    ImmutableModel,
    ItemId,
    PositiveInt,
    Sha256,
    ShortText,
    Version,
    content_hash,
    portable_relative_path,
    unique,
)

Stage = Literal["routing", "planning", "semantic_filter", "generation"]
Language = Literal["fr", "en", "multilingual"]
Authority = Literal["accepted_user_method", "reviewed_method", "proposal"]
Terms = Annotated[tuple[ShortText, ...], Field(max_length=20)]
ItemIds = Annotated[tuple[ItemId, ...], Field(max_length=20)]
Instruction = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]


class Provenance(ImmutableModel):
    document: str
    section: ShortText
    revision_sha256: Sha256

    _safe_document = field_validator("document")(portable_relative_path)

    @field_validator("document")
    @classmethod
    def method_source_only(cls, value: str) -> str:
        if not (
            value == "AGENTS.md"
            or value.startswith("docs/")
            and value.endswith(".md")
            or value.startswith("app/")
            and value.endswith(".py")
        ):
            raise ValueError("provenance must reference a method document or application source")
        return value


class MatchCriteria(ImmutableModel):
    all_terms: Terms = ()
    any_terms: Terms = ()
    facet_ids: Terms = ()
    language: Literal["fr", "en"] | None = None

    @field_validator("all_terms", "any_terms", "facet_ids")
    @classmethod
    def distinct_terms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return unique(values, "criteria terms")

    @property
    def constrained(self) -> bool:
        return bool(self.all_terms or self.any_terms or self.facet_ids or self.language)


class TaxonomyData(ImmutableModel):
    canonical_term: ShortText
    aliases_fr: Terms = ()
    aliases_en: Terms = ()
    ambiguity_terms: Terms = ()
    facet_kind: Literal["matrix", "process", "outcome", "condition", "term"]

    @field_validator("aliases_fr", "aliases_en", "ambiguity_terms")
    @classmethod
    def distinct_terms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return unique(values, "taxonomy terms")


class MethodPolicyData(ImmutableModel):
    instruction: Instruction
    applies_to: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=20)]


class GatewayData(ImmutableModel):
    criteria: MatchCriteria
    on_uncertain: Literal["keep_general_route", "record_ambiguity"] = "keep_general_route"

    @model_validator(mode="after")
    def meaningful_criteria(self) -> GatewayData:
        if not self.criteria.constrained:
            raise ValueError("gateway criteria must be constrained")
        return self


class RouteData(ImmutableModel):
    priority: int = Field(default=0, strict=True, ge=0, le=1000)
    match: MatchCriteria
    exclude: MatchCriteria = Field(default_factory=MatchCriteria)
    target_ids: Annotated[tuple[ItemId, ...], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def meaningful_targets(self) -> RouteData:
        unique(self.target_ids, "route targets")
        if not self.match.constrained:
            raise ValueError("route match must be constrained")
        return self


class RecipeStep(ImmutableModel):
    id: ShortText
    handler: Literal[
        "plan_hypothesis", "retrieve_grouped", "select_global_evidence", "synthesize_validated"
    ]
    knowledge_ids: ItemIds = ()
    input_contract: ShortText
    output_contract: ShortText

    @field_validator("knowledge_ids")
    @classmethod
    def distinct_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return unique(values, "step knowledge IDs")


class RecipeData(ImmutableModel):
    recipe_version: Version
    interaction_mode: Literal["research", "conversation"] = "research"
    steps: Annotated[tuple[RecipeStep, ...], Field(min_length=1, max_length=4)]

    @model_validator(mode="after")
    def fixed_execution_graph(self) -> RecipeData:
        unique(tuple(step.id for step in self.steps), "recipe step IDs")
        expected = (
            (
                "plan_hypothesis",
                "retrieve_grouped",
                "select_global_evidence",
                "synthesize_validated",
            )
            if self.interaction_mode == "research"
            else ("synthesize_validated",)
        )
        if tuple(step.handler for step in self.steps) != expected:
            raise ValueError("recipe handlers must preserve the approved execution graph")
        contracts = {
            "plan_hypothesis": ("question", "hypothesis_plan"),
            "retrieve_grouped": ("hypothesis_plan", "sqlite_candidates"),
            "select_global_evidence": ("sqlite_candidates", "sqlite_evidence"),
            "synthesize_validated": ("sqlite_evidence", "validated_answer"),
        }
        if any(
            (step.input_contract, step.output_contract) != contracts[step.handler]
            for step in self.steps
        ):
            raise ValueError("recipe input and output contracts must match their handler")
        return self


class SupersededItem(ImmutableModel):
    id: ItemId
    revision: PositiveInt


class KnowledgeItemBase(ImmutableModel):
    schema_version: Literal[1] = 1
    id: ItemId
    revision: PositiveInt
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    language: Language
    authority: Authority = "proposal"
    provenance: Annotated[tuple[Provenance, ...], Field(min_length=1, max_length=10)]
    depends_on: ItemIds = ()
    supersedes: SupersededItem | None = None
    stage: Stage
    required: bool = Field(default=False, strict=True)
    body: str = Field(default="", max_length=6000)

    @model_validator(mode="after")
    def stable_identity(self) -> KnowledgeItemBase:
        unique(self.depends_on, "dependencies")
        if self.id in self.depends_on:
            raise ValueError("item cannot depend on itself")
        if self.supersedes and (
            self.supersedes.id != self.id or self.supersedes.revision >= self.revision
        ):
            raise ValueError("supersedes must name an earlier revision of this item")
        if self.required and self.authority == "proposal":
            raise ValueError("a proposal cannot declare itself required")
        if self.required and not isinstance(self, MethodPolicyItem):
            raise ValueError("required items must be method policies")
        return self

    @property
    def content_sha256(self) -> str:
        return content_hash(self.model_dump(mode="json"))


class TaxonomyItem(KnowledgeItemBase):
    kind: Literal["taxonomy"]
    stage: Literal["planning"]
    data: TaxonomyData


class MethodPolicyItem(KnowledgeItemBase):
    kind: Literal["method_policy"]
    stage: Literal["planning", "semantic_filter", "generation"]
    data: MethodPolicyData


class GatewayItem(KnowledgeItemBase):
    kind: Literal["gateway"]
    stage: Literal["routing"]
    data: GatewayData


class RouteItem(KnowledgeItemBase):
    kind: Literal["route"]
    stage: Literal["routing"]
    data: RouteData

    @model_validator(mode="after")
    def declared_targets(self) -> RouteItem:
        if not set(self.data.target_ids) <= set(self.depends_on):
            raise ValueError("route targets must be declared dependencies")
        return self


class RecipeItem(KnowledgeItemBase):
    kind: Literal["recipe"]
    stage: Literal["routing"]
    data: RecipeData

    @model_validator(mode="after")
    def declared_knowledge(self) -> RecipeItem:
        referenced = {item_id for step in self.data.steps for item_id in step.knowledge_ids}
        if not referenced <= set(self.depends_on):
            raise ValueError("recipe knowledge must be declared dependencies")
        return self


KnowledgeItem = Annotated[
    TaxonomyItem | MethodPolicyItem | GatewayItem | RouteItem | RecipeItem,
    Field(discriminator="kind"),
]
KNOWLEDGE_ITEM_ADAPTER = TypeAdapter(KnowledgeItem)


class KnowledgePackage(ImmutableModel):
    schema_version: Literal[1] = 1
    package_id: ItemId
    version: Version
    minimum_app_version: Version
    minimum_schema_version: PositiveInt
    items: Annotated[tuple[KnowledgeItem, ...], Field(min_length=1, max_length=100)]

    @field_validator("items")
    @classmethod
    def canonical_items(cls, values: tuple[KnowledgeItem, ...]) -> tuple[KnowledgeItem, ...]:
        unique(tuple(item.id for item in values), "item IDs")
        return tuple(sorted(values, key=lambda item: item.id))

    @property
    def package_sha256(self) -> str:
        return content_hash(self.model_dump(mode="json"))
