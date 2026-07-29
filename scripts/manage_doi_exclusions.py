"""Inspect DOI exclusions or explicitly allow a rejected DOI to be harvested again."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import load_settings
from app.updates.doi_exclusions import DoiExclusionRegistry
from app.updates.models import normalize_doi


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    list_command = commands.add_parser("list", help="List DOI exclusion entries")
    list_command.add_argument("--all", action="store_true", help="Include reinstated DOI")
    reinstate = commands.add_parser("reinstate", help="Allow one DOI to be harvested again")
    reinstate.add_argument("doi")
    exclude = commands.add_parser("exclude", help="Explicitly exclude one DOI")
    exclude.add_argument("doi")
    exclude.add_argument("--title")
    exclude.add_argument("--reason", default="Exclusion explicite en ligne de commande.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    registry = DoiExclusionRegistry.for_database(settings.paths.database_path)
    if args.command == "reinstate":
        changed = registry.reinstate(args.doi)
        print("DOI réautorisé." if changed else "Ce DOI n'était pas exclu activement.")
        return 0 if changed else 1
    if args.command == "exclude":
        if normalize_doi(args.doi) is None:
            raise ValueError("DOI à exclure invalide.")
        registry.exclude(
            args.doi,
            title=args.title,
            reason=args.reason,
            origin="explicit_cli",
        )
        print("DOI exclu.")
        return 0

    entries = registry.document().entries
    if not args.all:
        entries = [entry for entry in entries if entry.active]
    print(
        json.dumps(
            [entry.model_dump(mode="json") for entry in entries], ensure_ascii=False, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
