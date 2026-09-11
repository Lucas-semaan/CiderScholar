from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.knowledge.wiki import ReasoningWikiError, load_reasoning_wiki

ROOT = Path(__file__).resolve().parents[1]


def test_wiki_always_loads_core_and_routes_two_relevant_pages_at_most() -> None:
    context = load_reasoning_wiki(
        "Comment choisir la clarification et piloter la fermentation du cidre ?"
    )

    assert context.page_ids[0] == "coeur"
    assert set(context.page_ids[1:]) == {"clarification", "fermentation"}
    assert len(context.page_ids) == 3
    assert len(context.content) <= 12_000
    assert "jamais des preuves de substitution" in context.content


def test_wiki_keeps_only_core_when_no_topic_term_matches() -> None:
    context = load_reasoning_wiki("Question cidricole générale sans terme spécialisé")

    assert context.page_ids == ("coeur",)


def test_wiki_rejects_a_page_changed_without_manifest_revision(tmp_path: Path) -> None:
    source = ROOT / "wiki"
    for path in source.iterdir():
        if path.is_file():
            (tmp_path / path.name).write_bytes(path.read_bytes())
    (tmp_path / "coeur.md").write_text("modification non déclarée", encoding="utf-8")

    with pytest.raises(ReasoningWikiError, match="hash mismatch"):
        load_reasoning_wiki("cidre", wiki_dir=tmp_path)


def test_all_manifest_sources_and_locators_are_resolved() -> None:
    manifest = json.loads((ROOT / "wiki" / "manifest.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "wiki" / "sources.json").read_text(encoding="utf-8"))
    source_index = {item["id"]: set(item["paragraph_ids"]) for item in sources["sources"]}

    assert len(source_index) == 194
    assert sum(bool(item["used_by"]) for item in sources["sources"]) == 80
    for page in manifest["pages"]:
        content = (ROOT / "wiki" / page["file"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == page["sha256"]
        assert page["source_refs"]
        for ref in page["source_refs"]:
            assert ref["locator"] in source_index[ref["source_id"]]
