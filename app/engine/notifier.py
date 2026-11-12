"""Admin notifications through the Bot, with per-key cooldown."""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("migrator.notify")


class Notifier:
    def __init__(self, chat_ids: tuple[int, ...], cooldown_seconds: int = 1800) -> None:
        self.chat_ids = chat_ids
        self.cooldown_seconds = cooldown_seconds
        self.bot: Any = None
        self._last_sent: dict[str, float] = {}

    async def notify(
        self,
        text: str,
        *,
        key: str | None = None,
        chat_id: int | None = None,
        buttons: Any = None,
    ) -> None:
        if key is not None:
            now = time.monotonic()
            last = self._last_sent.get(key)
            if last is not None and now - last < self.cooldown_seconds:
                return
            self._last_sent[key] = now
        if self.bot is None:
            log.warning("Notification (Bot offline): %s", text)
            return
        for target in ([chat_id] if chat_id else list(self.chat_ids)):
            try:
                kwargs: dict[str, Any] = {"parse_mode": None, "link_preview": False}
                if buttons is not None:
                    kwargs["buttons"] = buttons
                await self.bot.send_message(target, text, **kwargs)
            except Exception as exc:
                log.warning("Could not notify %s: %s", target, exc)

    def reset(self, key: str) -> None:
        self._last_sent.pop(key, None)
