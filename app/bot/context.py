"""Shared application context handed to every Bot handler."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import BootstrapConfig
from app.bot.ui import WizardManager
from app.db.repository import Repository
from app.engine.notifier import Notifier
from app.engine.scanner import Scanner
from app.engine.verifier import Verifier
from app.engine.watcher import Watcher
from app.engine.worker import Worker
from app.settings_store import SettingsStore
from app.telegram.client_manager import ClientManager
from app.telegram.limiter import TelegramLimiter


@dataclass
class PendingSelection:
    """A parsed link waiting for the admin to pick targets."""

    user_id: int
    chat: str
    topic_id: int | None
    start: int
    end: int
    selected: set[int] = field(default_factory=set)
    created: float = field(default_factory=time.monotonic)


@dataclass
class BotState:
    selections: dict[str, PendingSelection] = field(default_factory=dict)
    bind_codes: dict[str, tuple[int, float]] = field(default_factory=dict)
    import_modes: dict[int, str] = field(default_factory=dict)
    scan_tasks: dict[int, asyncio.Task] = field(default_factory=dict)
    wizards: WizardManager = field(default_factory=WizardManager)
    login: dict[int, Any] = field(default_factory=dict)

    def new_token(self) -> str:
        self._expire()
        return secrets.token_urlsafe(6)

    def new_bind_code(self, user_id: int, ttl: int = 600) -> str:
        self._expire()
        code = secrets.token_hex(4).upper()
        self.bind_codes[code] = (user_id, time.monotonic() + ttl)
        return code

    def use_bind_code(self, code: str) -> int | None:
        self._expire()
        entry = self.bind_codes.pop(code.strip().upper(), None)
        return entry[0] if entry else None

    def _expire(self) -> None:
        now = time.monotonic()
        for key in [k for k, v in self.selections.items() if now - v.created > 3600]:
            self.selections.pop(key, None)
        for key in [k for k, v in self.bind_codes.items() if v[1] < now]:
            self.bind_codes.pop(key, None)


@dataclass
class AppContext:
    boot: BootstrapConfig
    repo: Repository
    store: SettingsStore
    limiter: TelegramLimiter
    clients: ClientManager
    notifier: Notifier
    scanner: Scanner
    watcher: Watcher
    worker: Worker
    verifier: Verifier
    database_label: str
    state: BotState = field(default_factory=BotState)
    extras: dict[str, Any] = field(default_factory=dict)

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and int(user_id) in self.boot.admin_ids
