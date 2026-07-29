"""Shared low-overhead memory monitoring for sequential heavy operations."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from app.config import MemoryConfig

LOGGER = logging.getLogger(__name__)


class MemoryLimitError(RuntimeError):
    """Raised before a process would put the target machine at risk."""


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    process_rss_gb: float
    system_used_gb: float
    system_available_gb: float


class MemoryGuard:
    def __init__(self, config: MemoryConfig) -> None:
        self.config = config

    def snapshot(self) -> MemorySnapshot | None:
        try:
            import psutil
        except ImportError:
            return None
        memory = psutil.virtual_memory()
        return MemorySnapshot(
            process_rss_gb=psutil.Process(os.getpid()).memory_info().rss / (1024**3),
            system_used_gb=memory.used / (1024**3),
            system_available_gb=memory.available / (1024**3),
        )

    def check(self, operation: str) -> MemorySnapshot | None:
        snapshot = self.snapshot()
        if snapshot is None:
            return None
        if snapshot.system_used_gb >= self.config.warning_used_gb:
            LOGGER.warning(
                "Memory warning operation=%s system_used_gb=%.2f process_rss_gb=%.2f",
                operation,
                snapshot.system_used_gb,
                snapshot.process_rss_gb,
            )
        if (
            snapshot.process_rss_gb >= self.config.hard_process_limit_gb
            or snapshot.system_available_gb * 1024 < self.config.minimum_available_mb
        ):
            raise MemoryLimitError(f"insufficient memory to continue {operation} safely")
        return snapshot
