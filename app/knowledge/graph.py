"""Deterministic dependency traversal with an explicit reverse index."""

from __future__ import annotations

from dataclasses import dataclass

from app.knowledge.models import KnowledgePackage


class KnowledgeGraphError(ValueError):
    def __init__(self, code: str, item_ids: tuple[str, ...]) -> None:
        super().__init__(code)
        self.code = code
        self.item_ids = item_ids


@dataclass(frozen=True)
class KnowledgeGraph:
    dependencies: tuple[tuple[str, tuple[str, ...]], ...]

    @classmethod
    def build(cls, package: KnowledgePackage) -> KnowledgeGraph:
        graph = cls(tuple((item.id, tuple(sorted(item.depends_on))) for item in package.items))
        graph.closure(tuple(item.id for item in package.items))
        return graph

    def closure(self, item_ids: tuple[str, ...]) -> tuple[str, ...]:
        lookup = dict(self.dependencies)
        complete: set[str] = set()
        stack: list[str] = []

        def visit(item_id: str) -> None:
            if item_id not in lookup:
                raise KnowledgeGraphError("missing_dependency", (item_id,))
            if item_id in stack:
                raise KnowledgeGraphError("dependency_cycle", tuple(stack[stack.index(item_id) :]))
            if item_id in complete:
                return
            stack.append(item_id)
            for dependency in lookup[item_id]:
                visit(dependency)
            stack.pop()
            complete.add(item_id)

        for item_id in sorted(item_ids):
            visit(item_id)
        return tuple(sorted(complete))

    def referenced_by(self, item_id: str) -> tuple[str, ...]:
        if item_id not in dict(self.dependencies):
            raise KnowledgeGraphError("unknown_item", (item_id,))
        return tuple(key for key, dependencies in self.dependencies if item_id in dependencies)

    def affected_by(self, item_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Include changed nodes and all consumers, without treating backlinks as dependencies."""

        affected: set[str] = set()
        pending = list(item_ids)
        while pending:
            item_id = pending.pop()
            if item_id not in affected:
                pending.extend(self.referenced_by(item_id))
                affected.add(item_id)
        return tuple(sorted(affected))
