from __future__ import annotations

from app.evaluation.second_wave_checkpoint import (
    SecondWaveCheckpoint,
    checkpoint_is_current,
    seal_checkpoint,
)


def test_second_wave_checkpoint_resumes_only_the_next_missing_stage() -> None:
    checkpoint = SecondWaveCheckpoint(corpus_sha256="a" * 64, retrieval_version="hybrid-v1")
    assert checkpoint.next_stage() == "first_wave"
    sealed = seal_checkpoint(checkpoint)
    assert checkpoint_is_current(sealed, corpus_sha256="a" * 64, retrieval_version="hybrid-v1")
    assert not checkpoint_is_current(sealed, corpus_sha256="b" * 64, retrieval_version="hybrid-v1")
    assert not checkpoint_is_current(sealed, corpus_sha256="a" * 64, retrieval_version="hybrid-v2")
