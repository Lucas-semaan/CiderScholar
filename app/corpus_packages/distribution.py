"""Local OneDrive/SharePoint distribution layout with no credential storage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import Settings


@dataclass(frozen=True, slots=True)
class DistributionPaths:
    root: Path
    installers: Path
    corpus: Path
    suggestions_inbox: Path
    archive: Path


class DistributionPathError(RuntimeError):
    """The configured synchronized directory is absent or unexpectedly located."""


def distribution_paths(root: Path) -> DistributionPaths:
    resolved = root.resolve()
    return DistributionPaths(
        root=resolved,
        installers=resolved / "installers",
        corpus=resolved / "corpus",
        suggestions_inbox=resolved / "suggestions" / "inbox",
        archive=resolved / "archive",
    )


def create_distribution_layout(root: Path) -> DistributionPaths:
    """Create only public distribution directories, never configuration or secret files."""

    paths = distribution_paths(root)
    for directory in (
        paths.installers,
        paths.corpus,
        paths.suggestions_inbox,
        paths.archive,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def validate_distribution_root(
    settings: Settings,
    *,
    explicit_confirmation: bool = False,
) -> Path:
    """Accept the expected folder name or a deliberate user-confirmed alternative."""

    configured = settings.distribution.synchronized_root
    if not settings.distribution.enabled or configured is None:
        raise DistributionPathError("corpus distribution is not configured")
    root = configured.resolve()
    if not root.is_dir():
        raise DistributionPathError("synchronized distribution directory is unavailable")
    data_root = settings.paths.data_dir.resolve()
    if root == data_root or root.is_relative_to(data_root):
        raise DistributionPathError(
            "distribution directory cannot be inside local application data"
        )
    if (
        root.name.casefold() != settings.distribution.expected_folder_name.casefold()
        and not explicit_confirmation
    ):
        raise DistributionPathError(
            "synchronized directory name is unexpected and requires explicit confirmation"
        )
    return root
