"""Per-operation pacing and FloodWait handling for every Telegram call."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Awaitable, Callable

from telethon import errors

from app.errors import FloodWaitDeferred
from app.settings_store import SettingsStore

log = logging.getLogger("migrator.limiter")


class TelegramLimiter:
    def __init__(self, store: SettingsStore) -> None:
        self.store = store
        self._lock = asyncio.Lock()
        self._last_global = 0.0
        self._last_by_operation: dict[str, float] = {}

    async def wait(self, operation: str) -> None:
        limits = self.store.current.limits
        async with self._lock:
            now = time.monotonic()
            global_wait = limits.global_min_delay_seconds - (now - self._last_global)
            op_wait = limits.delay_for(operation) - (now - self._last_by_operation.get(operation, 0.0))
            delay = max(0.0, global_wait, op_wait)
            if delay > 0:
                await asyncio.sleep(delay)
            finished = time.monotonic()
            self._last_global = finished
            self._last_by_operation[operation] = finished

    async def call(self, operation: str, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        while True:
            await self.wait(operation)
            try:
                return await fn(*args, **kwargs)
            except (errors.FloodWaitError, errors.SlowModeWaitError) as exc:
                await self._handle_flood(int(getattr(exc, "seconds", 60) or 60))

    async def _handle_flood(self, seconds: int) -> None:
        limits = self.store.current.limits
        low = min(limits.floodwait_extra_min_seconds, limits.floodwait_extra_max_seconds)
        high = max(limits.floodwait_extra_min_seconds, limits.floodwait_extra_max_seconds)
        wait = seconds + random.randint(low, high)
        if wait > limits.floodwait_defer_after_seconds:
            log.warning("FloodWait %ss from Telegram: deferring the current job", wait)
            raise FloodWaitDeferred(wait)
        log.warning("FloodWait from Telegram: sleeping %ss", wait)
        await asyncio.sleep(wait)
