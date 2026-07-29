"""Publish and archive one verified immutable common-corpus package."""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.config import load_settings
from app.corpora import CorpusScope, authorize_corpus_mutation, load_local_profile
from app.corpus_packages.publisher import (
    CorpusArchiveReport,
    CorpusPublishReport,
    archive_published_package,
    publish_corpus_package,
)


class CorpusPublishCommandReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publication: CorpusPublishReport
    protected_archive: CorpusArchiveReport


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version_directory", type=Path)
    parser.add_argument("--confirm-alternative-path", action="store_true")
    arguments = parser.parse_args(argv)
    profile = load_local_profile()
    authorize_corpus_mutation(CorpusScope.COMMON, profile)
    settings = load_settings()
    publication = publish_corpus_package(
        settings,
        arguments.version_directory,
        profile=profile,
        explicit_path_confirmation=arguments.confirm_alternative_path,
    )
    archived = archive_published_package(
        settings,
        Path(publication.version_directory),
        profile=profile,
    )
    print(
        CorpusPublishCommandReport(
            publication=publication,
            protected_archive=archived,
        ).model_dump_json(indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
