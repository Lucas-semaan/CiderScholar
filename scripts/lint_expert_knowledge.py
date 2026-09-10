"""Validate a local expert-instruction package without database or network access."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from uuid import uuid4

from app.knowledge.loader import lint_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-dir", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = lint_package(args.knowledge_dir, source_root=args.source_root)
    serialized = report.model_dump_json(indent=2) + "\n"
    if args.output is not None:
        # An output cannot overwrite a package file or one of its provenance documents.
        root = (args.source_root or args.knowledge_dir.parent).resolve()
        output = args.output.resolve()
        permitted = (root / "data" / "exports").resolve()
        knowledge_root = args.knowledge_dir.resolve()
        if (
            not output.is_relative_to(permitted)
            or output.is_relative_to(knowledge_root)
            or output.suffix != ".json"
        ):
            parser.error("--output must be a JSON file under source-root/data/exports")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(serialized, encoding="utf-8")
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    print(serialized, end="")
    return 0 if report.structurally_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
