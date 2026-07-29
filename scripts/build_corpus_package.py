"""Build one immutable, verified common-corpus package as strict JSON."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.config import load_settings
from app.corpora import CorpusScope, authorize_corpus_mutation, load_local_profile
from app.corpus_packages.builder import build_corpus_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    authorize_corpus_mutation(CorpusScope.COMMON, load_local_profile())
    report = build_corpus_package(load_settings(), output_root=arguments.output)
    print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
