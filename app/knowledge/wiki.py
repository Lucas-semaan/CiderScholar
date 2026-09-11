"""Bounded, versioned access to the local reasoning wiki.

The wiki frames reasoning but is never scientific evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_CONTEXT_CHARACTERS = 12_000
MAX_SELECTED_TOPICAL_PAGES = 2
_WORD = re.compile(r"[a-z0-9]+")


class ReasoningWikiError(ValueError):
    """The local wiki cannot be trusted or safely loaded."""


@dataclass(frozen=True, slots=True)
class ReasoningWikiContext:
    content: str
    version: str
    manifest_sha256: str
    page_ids: tuple[str, ...]


def default_wiki_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "wiki"


def _normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(_WORD.findall("".join(c for c in folded if not unicodedata.combining(c))))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReasoningWikiError("reasoning wiki manifest is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise ReasoningWikiError("reasoning wiki manifest must be an object")
    return value


def _safe_page(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9-]+\.md", value):
        raise ReasoningWikiError("reasoning wiki page path is invalid")
    path = (root / value).resolve()
    if path.parent != root.resolve() or path.is_symlink() or not path.is_file():
        raise ReasoningWikiError("reasoning wiki page is unavailable")
    return path


def _page_score(question: str, terms: object) -> int:
    if not isinstance(terms, list) or any(not isinstance(term, str) for term in terms):
        raise ReasoningWikiError("reasoning wiki terms are invalid")
    normalized_question = f" {_normalized(question)} "
    return sum(
        max(1, len(_normalized(term).split()))
        for term in terms
        if _normalized(term) and f" {_normalized(term)} " in normalized_question
    )


def _source_index(root: Path) -> dict[str, set[str]]:
    registry = _read_json(root / "sources.json")
    sources = registry.get("sources")
    if registry.get("schema_version") != 1 or not isinstance(sources, list):
        raise ReasoningWikiError("reasoning wiki source registry is invalid")
    index: dict[str, set[str]] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise ReasoningWikiError("reasoning wiki source entry is invalid")
        source_id = source.get("id")
        sha256 = source.get("sha256")
        locators = source.get("paragraph_ids")
        if (
            not isinstance(source_id, str)
            or not re.fullmatch(r"src-[0-9a-f]{12}", source_id)
            or source_id in index
            or not isinstance(sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
            or not isinstance(locators, list)
            or any(not isinstance(locator, str) for locator in locators)
        ):
            raise ReasoningWikiError("reasoning wiki source entry is invalid")
        index[source_id] = set(locators)
    return index


def _validate_source_refs(entry: dict[str, Any], source_index: dict[str, set[str]]) -> None:
    refs = entry.get("source_refs")
    if not isinstance(refs, list) or not refs:
        raise ReasoningWikiError("reasoning wiki page has no source references")
    for ref in refs:
        if not isinstance(ref, dict):
            raise ReasoningWikiError("reasoning wiki source reference is invalid")
        source_id = ref.get("source_id")
        locator = ref.get("locator")
        if source_id not in source_index or locator not in source_index[source_id]:
            raise ReasoningWikiError("reasoning wiki source reference is unresolved")


def load_reasoning_wiki(
    question: str,
    *,
    wiki_dir: Path | None = None,
) -> ReasoningWikiContext:
    """Load the core plus at most two matching pages after verifying their hashes."""

    root = (wiki_dir or default_wiki_dir()).resolve()
    manifest_path = root / "manifest.json"
    manifest_bytes = manifest_path.read_bytes() if manifest_path.is_file() else b""
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("version"), str):
        raise ReasoningWikiError("reasoning wiki manifest version is invalid")
    pages = manifest.get("pages")
    core_id = manifest.get("core")
    if not isinstance(pages, list) or not pages or not isinstance(core_id, str):
        raise ReasoningWikiError("reasoning wiki page list is invalid")
    source_index = _source_index(root)
    parsed: list[tuple[dict[str, Any], Path, str]] = []
    seen: set[str] = set()
    for entry in pages:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ReasoningWikiError("reasoning wiki page entry is invalid")
        page_id = entry["id"]
        if page_id in seen:
            raise ReasoningWikiError("reasoning wiki contains duplicate page ids")
        seen.add(page_id)
        path = _safe_page(root, entry.get("file"))
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
            raise ReasoningWikiError("reasoning wiki page hash mismatch")
        _validate_source_refs(entry, source_index)
        parsed.append((entry, path, raw.decode("utf-8")))
    core = next((item for item in parsed if item[0]["id"] == core_id), None)
    if core is None:
        raise ReasoningWikiError("reasoning wiki core page is missing")
    ranked = sorted(
        (
            (_page_score(question, entry.get("terms")), entry["id"], text)
            for entry, _, text in parsed
            if entry["id"] != core_id
        ),
        key=lambda item: (-item[0], item[1]),
    )
    topical = [item for item in ranked if item[0] > 0][:MAX_SELECTED_TOPICAL_PAGES]
    chosen = [(core[0]["id"], core[2]), *((page_id, text) for _, page_id, text in topical)]
    content = "\n\n".join(text.strip() for _, text in chosen)
    if len(content) > MAX_CONTEXT_CHARACTERS:
        content = content[:MAX_CONTEXT_CHARACTERS].rsplit("\n", 1)[0]
    return ReasoningWikiContext(
        content=content,
        version=manifest["version"],
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        page_ids=tuple(page_id for page_id, _ in chosen),
    )
