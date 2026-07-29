"""Strict, versioned contracts for a distributable common corpus."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"
APP_VERSION_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$"
CORPUS_VERSION_PATTERN = r"^corpus-v1-[0-9a-f]{64}$"


class PackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactDigest(PackageModel):
    relative_path: str = Field(min_length=1, max_length=500)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_PATTERN)
    kind: Literal["sqlite", "qdrant", "metadata"]

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("artifact path must be a safe POSIX relative path")
        return path.as_posix()


class ArchiveDigest(PackageModel):
    filename: Literal["corpus.zip"] = "corpus.zip"
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=SHA256_PATTERN)


class CorpusCounts(PackageModel):
    articles: int = Field(ge=0)
    chunks: int = Field(ge=0)
    vectors: int = Field(ge=0)


class CorpusManifest(PackageModel):
    format_version: Literal[1] = 1
    corpus_version: str = Field(pattern=CORPUS_VERSION_PATTERN)
    published_at: datetime
    schema_version: int = Field(ge=1)
    minimum_app_version: str = Field(pattern=APP_VERSION_PATTERN)
    counts: CorpusCounts
    artifacts: list[ArtifactDigest] = Field(min_length=1)
    archive: ArchiveDigest

    @field_validator("published_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def unique_artifact_paths(self) -> CorpusManifest:
        paths = [artifact.relative_path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact paths must be unique")
        return self


class LatestCorpusPointer(PackageModel):
    format_version: Literal[1] = 1
    corpus_version: str = Field(pattern=CORPUS_VERSION_PATTERN)
    published_at: datetime
    manifest_relative_path: str = Field(pattern=r"^corpus-v1-[0-9a-f]{64}/manifest\.json$")
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
