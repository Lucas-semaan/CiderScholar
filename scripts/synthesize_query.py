"""Create or resume a hierarchical synthesis from persisted article evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.llm.argo_client import ArgoClient
from app.llm.final_synthesis import HierarchicalSynthesisService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query_id", help="Completed or partially completed evidence query UUID")
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Discard an existing synthesis run and regenerate every theme",
    )
    parser.add_argument("--json", action="store_true", help="Print structured JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = settings_for_corpus(load_settings(args.config), CorpusScope.COMMON)
    database = Database(settings.paths.database_path)
    database.initialize()
    try:
        with ArgoClient(settings) as llm:
            execution = HierarchicalSynthesisService(settings, database, llm).synthesize(
                query_id=args.query_id, resume=not args.no_resume
            )
    except Exception as exc:
        print(
            f"Synthèse échouée: {type(exc).__name__}: {str(exc)[:1000]}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(execution.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0
    print("MODE HORS LIGNE ACTIF" if settings.app.offline_mode else "MODE EN LIGNE")
    print(
        f"query_id={args.query_id} | appels_llm={execution.llm_calls} | "
        f"thèmes_repris={execution.resumed_theme_count} | "
        f"durée={execution.duration_seconds:.3f}s",
        file=sys.stderr,
    )
    print(execution.result.answer_markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
