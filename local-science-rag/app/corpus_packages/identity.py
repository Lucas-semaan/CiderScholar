"""Content-addressed, deterministic common-corpus version identifiers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from app.corpus_packages.models import ArtifactDigest, CorpusCounts


def corpus_content_sha256(
    *,
    schema_version: int,
    minimum_app_version: str,
    counts: CorpusCounts,
    artifacts: Sequence[ArtifactDigest],
) -> str:
    """Hash only immutable logical content, excluding dates and archive metadata."""

    payload = {
        "format_version": 1,
        "schema_version": schema_version,
        "minimum_app_version": minimum_app_version,
        "counts": counts.model_dump(mode="json"),
        "artifacts": [
            artifact.model_dump(mode="json")
            for artifact in sorted(artifacts, key=lambda item: item.relative_path)
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def corpus_version_id(
    *,
    schema_version: int,
    minimum_app_version: str,
    counts: CorpusCounts,
    artifacts: Sequence[ArtifactDigest],
) -> str:
    digest = corpus_content_sha256(
        schema_version=schema_version,
        minimum_app_version=minimum_app_version,
        counts=counts,
        artifacts=artifacts,
    )
    return f"corpus-v1-{digest}"
