from __future__ import annotations

import pytest

from app.corpora import CorpusMutationForbiddenError, LocalProfile
from app.services.corpus_updates import (
    activate_prepared_common_corpus,
)


def test_common_directory_swap_retains_the_previous_common_corpus(settings) -> None:
    old_common = settings.paths.common_dir / "old.txt"
    old_common.write_text("old common", encoding="utf-8")
    prepared = settings.paths.data_dir / "staging" / "common-next"
    prepared.mkdir(parents=True)
    (prepared / "new.txt").write_text("new common", encoding="utf-8")

    swap = activate_prepared_common_corpus(
        settings,
        prepared,
        profile=LocalProfile.ADMIN,
    )

    assert (swap.activated_path / "new.txt").read_text(encoding="utf-8") == "new common"
    assert swap.previous_path is not None
    assert (swap.previous_path / "old.txt").read_text(encoding="utf-8") == "old common"


def test_common_directory_swap_is_admin_only(settings) -> None:
    prepared = settings.paths.data_dir / "staging" / "common-next"
    prepared.mkdir(parents=True)

    with pytest.raises(CorpusMutationForbiddenError):
        activate_prepared_common_corpus(
            settings,
            prepared,
            profile=LocalProfile.USER,
        )

    assert prepared.is_dir()
    assert settings.paths.common_dir.is_dir()
