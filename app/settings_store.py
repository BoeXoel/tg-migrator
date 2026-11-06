"""In-memory cache of runtime settings backed by the database."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.config import RuntimeSettings, set_setting
from app.db.repository import Repository


class SettingsStore:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.current = RuntimeSettings()
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[RuntimeSettings], Any]] = []

    def subscribe(self, callback: Callable[[RuntimeSettings], Any]) -> None:
        self._listeners.append(callback)

    async def load(self) -> RuntimeSettings:
        self.current = await self.repo.load_settings()
        # Persist defaults on first start so exports show the full structure.
        await self.repo.save_settings(self.current)
        return self.current

    async def set(self, path: str, value: Any) -> RuntimeSettings:
        async with self._lock:
            updated = set_setting(self.current, path, value)
            await self.repo.save_settings(updated)
            self.current = updated
        self._notify()
        return self.current

    async def replace(self, settings: RuntimeSettings) -> None:
        async with self._lock:
            await self.repo.save_settings(settings)
            self.current = settings
        self._notify()

    def _notify(self) -> None:
        for callback in self._listeners:
            try:
                callback(self.current)
            except Exception:  # pragma: no cover - listeners must not break saving
                pass


def effective_settings(base: RuntimeSettings, overrides: dict[str, Any] | None) -> RuntimeSettings:
    """Apply route-level dotted overrides on top of the global settings."""

    settings = base
    for path, value in (overrides or {}).items():
        try:
            settings = set_setting(settings, path, value)
        except (KeyError, TypeError, ValueError):
            continue
    return settings
