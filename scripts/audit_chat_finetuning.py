"""Audit one durable prompt-profile evaluation run without calling external services."""

from __future__ import annotations

import argparse

from app.evaluation.chat_finetuning import audit_evaluation_run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--database", default="data/database/science_rag.sqlite3")
    args = parser.parse_args()
    audit = audit_evaluation_run(args.database, args.run_id)
    print(audit.model_dump_json(indent=2))
    return 0 if audit.reliable else 1


if __name__ == "__main__":
    raise SystemExit(main())
