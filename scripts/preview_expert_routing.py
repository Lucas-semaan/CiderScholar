"""Preview local expert routing, without altering chatbot settings or accessing a corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import ExpertMemoryConfig
from app.knowledge.loader import KnowledgeLoadError, load_package
from app.knowledge.routing import preview_routing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-dir", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--question", required=True)
    parser.add_argument("--language", choices=("fr", "en"))
    parser.add_argument("--facet-id", action="append", default=[])
    parser.add_argument(
        "--include-proposals",
        action="store_true",
        help="Preview candidate routes without approving them.",
    )
    args = parser.parse_args(argv)
    preview_metadata = {
        "preview_only": True,
        "scientific_approval": False,
        "include_proposals": args.include_proposals,
    }
    try:
        package = load_package(args.knowledge_dir, source_root=args.source_root)
        result = preview_routing(
            args.question,
            package,
            config=ExpertMemoryConfig(mode="shadow"),
            language=args.language,
            facet_ids=tuple(args.facet_id),
            include_proposals=args.include_proposals,
        )
        # Display identities, choices and budgets, not the question or private context text.
        projection = result.model_dump(mode="json", exclude={"contexts"})
        projection["stage_characters"] = {
            context.stage: context.characters for context in result.contexts
        }
        print(json.dumps(projection, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except KnowledgeLoadError as error:
        print(
            json.dumps(
                {**preview_metadata, "issues": [issue.model_dump() for issue in error.issues]}
            )
        )
        return 1
    except ValueError:
        print(json.dumps({**preview_metadata, "code": "invalid_routing_input"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
