"""Small immutable primitives shared by expert-memory contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ItemId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,5}$", max_length=120),
]
Version = Annotated[
    str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]


class ImmutableModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_default=True, allow_inf_nan=False
    )

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def content_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def unique[T](values: tuple[T, ...], label: str) -> tuple[T, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label}")
    return values


def portable_relative_path(value: str) -> str:
    """Reject platform-specific paths before any filesystem access."""

    windows = PureWindowsPath(value)
    parts = value.split("/")
    if (
        not value
        or len(value) > 300
        or "\\" in value
        or ":" in value
        or windows.drive
        or windows.root
        or PurePosixPath(value).is_absolute()
        or any(part in {"", ".", ".."} or part.rstrip(". ") != part for part in parts)
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("unsafe relative path")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if any(part.split(".")[0].upper() in reserved for part in parts):
        raise ValueError("reserved relative path")
    return value
