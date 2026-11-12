"""Telethon client lifecycle: Bot (control + optional writer) and user reader."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from telethon import TelegramClient, errors, utils
from telethon.tl import types

from app import __version__
from app.config import BootstrapConfig
from app.errors import PeerUnresolvedError, ReaderUnavailableError
from app.telegram.limiter import TelegramLimiter

log = logging.getLogger("migrator.telegram")

BOT_UPLOAD_LIMIT_BYTES = 2_000 * 1024 * 1024
USER_UPLOAD_LIMIT_BYTES = 2_000 * 1024 * 1024
PREMIUM_UPLOAD_LIMIT_BYTES = 4_000 * 1024 * 1024
_NUMERIC_RE = re.compile(r"^-?\d+$")
_PRIVATE_INVITE_RE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/(?:\+|joinchat/)", re.IGNORECASE)


@dataclass(frozen=True)
class AccountCapabilities:
    identity: str
    account_type: str  # bot | user | premium_user
    account_id: int
    max_upload_bytes: int
    caption_limit: int

    @property
    def is_bot(self) -> bool:
        return self.account_type == "bot"


def capabilities_for(me: Any, override_limit: int = 0) -> AccountCapabilities:
    is_bot = bool(getattr(me, "bot", False))
    is_premium = bool(getattr(me, "premium", False)) and not is_bot
    if override_limit:
        limit = override_limit
    elif is_bot:
        limit = BOT_UPLOAD_LIMIT_BYTES
    elif is_premium:
        limit = PREMIUM_UPLOAD_LIMIT_BYTES
    else:
        limit = USER_UPLOAD_LIMIT_BYTES
    account_type = "bot" if is_bot else "premium_user" if is_premium else "user"
    return AccountCapabilities(
        identity=f"{account_type}:{me.id}",
        account_type=account_type,
        account_id=int(me.id),
        max_upload_bytes=limit,
        caption_limit=4096 if is_premium else 1024,
    )


def peer_value(chat: str | int) -> str | int:
    """Numeric IDs must be ints for Telethon; usernames/links stay strings."""

    text = str(chat).strip()
    if _NUMERIC_RE.fullmatch(text):
        return int(text)
    return text


def is_private_invite_link(value: str | int) -> bool:
    return bool(_PRIVATE_INVITE_RE.match(str(value).strip()))


def display_name(entity: Any) -> str:
    return utils.get_display_name(entity) or str(utils.get_peer_id(entity))


def canonical_id(entity: Any) -> str:
    return str(utils.get_peer_id(entity))


def _secure_session_file(path: Path) -> None:
    for candidate in (path, path.with_name(path.name + "-journal")):
        try:
            if candidate.exists():
                os.chmod(candidate, 0o600)
        except OSError:
            pass


class ClientManager:
    def __init__(self, boot: BootstrapConfig, limiter: TelegramLimiter) -> None:
        self.boot = boot
        self.limiter = limiter
        self.bot: TelegramClient | None = None
        self.bot_me: Any = None
        self.reader: TelegramClient | None = None
        self.reader_me: Any = None
        self._entity_cache: dict[tuple[int, str], Any] = {}
        self._dialogs_loaded: set[int] = set()
        self._reader_callbacks: list[Callable[[], Awaitable[None]]] = []
        store = getattr(self.limiter, "store", None)
        if store is not None and hasattr(store, "subscribe"):
            store.subscribe(self._sync_flood_threshold)

    # ---------------------------------------------------------------- factory

    def _flood_sleep_threshold(self) -> int:
        store = getattr(self.limiter, "store", None)
        if store is None:
            return 60
        return max(0, int(store.current.limits.floodwait_defer_after_seconds))

    def _sync_flood_threshold(self, settings: Any) -> None:
        threshold = max(0, int(settings.limits.floodwait_defer_after_seconds))
        for client in (self.bot, self.reader):
            if client is not None:
                client.flood_sleep_threshold = threshold

    def session_path(self, name: str) -> Path:
        return self.boot.sessions_dir / f"{name}.session"

    def make_client(self, session_name: str) -> TelegramClient:
        client = TelegramClient(
            str(self.boot.sessions_dir / session_name),
            self.boot.api_id,
            self.boot.api_hash,
            proxy=self.boot.proxy.telethon_proxy(),
            # Allow chunk loops (GetFileRequest / SaveBigFilePartRequest) to sleep
            # in-place on short FloodWait / FloodPremiumWait up to floodwait_defer_after_seconds;
            # longer waits raise out to TelegramLimiter / Worker for job deferral.
            flood_sleep_threshold=self._flood_sleep_threshold(),
            request_retries=10,
            raise_last_call_error=True,
            device_model="TG Migrator",
            app_version=__version__,
            connection_retries=10,
            retry_delay=5,
            auto_reconnect=True,
        )
        return client

    # -------------------------------------------------------------- lifecycle

    async def start_bot(self) -> TelegramClient:
        bot = self.make_client(self.boot.bot_session_name)
        await bot.start(bot_token=self.boot.bot_token)
        _secure_session_file(self.session_path(self.boot.bot_session_name))
        self.bot = bot
        self.bot_me = await bot.get_me()
        log.info("Bot online: @%s (%s)", getattr(self.bot_me, "username", ""), self.bot_me.id)
        return bot

    async def start_reader(self) -> bool:
        """Connect the user session if it is already authorised."""

        client = self.make_client(self.boot.user_session_name)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            log.warning("User session is not logged in; send /login to the Bot")
            return False
        await self.adopt_reader(client)
        return True

    async def adopt_reader(self, client: TelegramClient) -> None:
        client.flood_sleep_threshold = self._flood_sleep_threshold()
        self.reader = client
        self.reader_me = await client.get_me()
        _secure_session_file(self.session_path(self.boot.user_session_name))
        log.info("Reader account: %s (%s)", display_name(self.reader_me), self.reader_me.id)
        for callback in list(self._reader_callbacks):
            try:
                await callback()
            except Exception:
                log.exception("Reader-ready callback failed")

    def on_reader_ready(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._reader_callbacks.append(callback)

    async def logout_reader(self) -> None:
        if self.reader is not None:
            try:
                await self.reader.log_out()
            finally:
                self.reader = None
                self.reader_me = None
        path = self.session_path(self.boot.user_session_name)
        path.unlink(missing_ok=True)

    async def stop(self) -> None:
        for client in (self.reader, self.bot):
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    # ------------------------------------------------------------ properties

    def require_reader(self) -> TelegramClient:
        if self.reader is None:
            raise ReaderUnavailableError()
        return self.reader

    @property
    def writer(self) -> TelegramClient:
        if self.boot.use_bot_as_writer and self.bot is not None:
            return self.bot
        return self.require_reader()

    @property
    def writer_is_reader(self) -> bool:
        return self.reader is not None and self.writer is self.reader

    def writer_capabilities(self, override_limit: int = 0) -> AccountCapabilities:
        me = self.bot_me if self.writer is self.bot else self.reader_me
        return capabilities_for(me, override_limit)

    def reader_capabilities(self, override_limit: int = 0) -> AccountCapabilities | None:
        return capabilities_for(self.reader_me, override_limit) if self.reader_me is not None else None

    # ----------------------------------------------------- entity resolution

    async def resolve(self, client: TelegramClient, chat: str | int) -> Any:
        """Resolve a chat for a specific client, warming the cache if needed."""

        value = peer_value(chat)
        key = (id(client), str(value).lower())
        if key in self._entity_cache:
            return self._entity_cache[key]

        is_bot = client is self.bot
        try:
            entity = await self.limiter.call("resolve", client.get_entity, value)
        except (ValueError, errors.PeerIdInvalidError, errors.ChannelInvalidError) as exc:
            if is_bot:
                raise PeerUnresolvedError(str(chat)) from exc
            if id(client) in self._dialogs_loaded:
                raise
            # A user session learns access hashes from its dialog list.
            log.info("Loading dialogs to resolve %s", chat)
            await self.limiter.call("read", client.get_dialogs)
            self._dialogs_loaded.add(id(client))
            entity = await self.limiter.call("resolve", client.get_entity, value)

        self._entity_cache[key] = entity
        if isinstance(entity, (types.Channel, types.Chat, types.User)):
            self._entity_cache[(id(client), canonical_id(entity))] = entity
        return entity

    def forget_entity(self, client: TelegramClient, chat: str | int) -> None:
        self._entity_cache.pop((id(client), str(peer_value(chat)).lower()), None)


async def interactive_cli_login(boot: BootstrapConfig) -> None:
    """Terminal login: ``docker compose run --rm migrator login``."""

    boot.ensure_directories()
    client = TelegramClient(
        str(boot.sessions_dir / boot.user_session_name),
        boot.api_id,
        boot.api_hash,
        proxy=boot.proxy.telethon_proxy(),
        device_model="TG Migrator",
        app_version=__version__,
    )
    await client.start(
        phone=lambda: input("Phone number (international format, e.g. +8613800000000): ").strip(),
    )
    me = await client.get_me()
    _secure_session_file(boot.sessions_dir / f"{boot.user_session_name}.session")
    print(f"Logged in as {display_name(me)} ({me.id}). Session saved; restart the service container.")
    await client.disconnect()
