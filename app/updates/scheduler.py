"""Lightweight application-lifetime scheduling; heavy work belongs to the durable worker."""

from __future__ import annotations

import asyncio
import logging

from app.config import Settings
from app.corpora import LocalProfile, load_local_profile
from app.updates.watch_store import WatchStore

LOGGER = logging.getLogger(__name__)


async def run_watch_scheduler(settings: Settings) -> None:
    if load_local_profile() is not LocalProfile.ADMIN:
        return
    store = WatchStore(settings)
    while True:
        try:
            await asyncio.to_thread(store.enqueue)
        except Exception as error:
            LOGGER.warning("bibliographic_watch.schedule_failed type=%s", type(error).__name__)
        await asyncio.sleep(60)
