"""Validate the reasoning wiki without opening SQLite or loading a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.knowledge.wiki import load_reasoning_wiki


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki-dir", type=Path, default=Path("wiki"))
    arguments = parser.parse_args()
    context = load_reasoning_wiki(
        "clarification fermentation assemblage hygiène", wiki_dir=arguments.wiki_dir
    )
    print(
        json.dumps(
            {
                "valid": True,
                "version": context.version,
                "manifest_sha256": context.manifest_sha256,
                "core_loaded": context.page_ids[0] == "coeur",
                "context_characters": len(context.content),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
