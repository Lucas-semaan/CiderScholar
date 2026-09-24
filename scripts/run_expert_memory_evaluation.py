"""Prepare/resume the bounded base/candidate campaign plan for expert-memory evaluation."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.prepare_expert_memory_evaluation import build_parser, main

__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
