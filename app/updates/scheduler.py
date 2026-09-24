"""Lightweight application-lifetime scheduling; heavy work belongs to the durable worker."""

from __future__ import annotations

import asyncio
import logging

from app.config import Settings
from app.corpora import LocalProfile, load_local_profile
from app.updates.watch_store import WatchStore

LOGGER = logging.getLogger(__name__)
SCHEDULER_INTERVAL_SECONDS = 60


async def run_watch_scheduler(settings: Settings) -> None:
    if load_local_profile() is not LocalProfile.ADMIN:
        return
    store = WatchStore(settings)
    # Do not race the API lifespan's first administrative configuration call.
    # The scheduler is periodic by contract; a one-minute initial wait also
    # leaves startup migrations and durable configuration writes uncontended.
    await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)
    while True:
        try:
            await asyncio.to_thread(store.enqueue)
        except Exception as error:
            LOGGER.warning("bibliographic_watch.schedule_failed type=%s", type(error).__name__)
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)
