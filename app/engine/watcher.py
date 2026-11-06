"""Live monitoring of watched routes plus watermark-based catch-up."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import TelegramClient, events

from app.db.models import Route
from app.db.repository import Repository
from app.engine.scanner import EnqueueStats, Scanner, SourceInfo
from app.settings_store import SettingsStore, effective_settings
from app.telegram import media_helper as mh
from app.telegram.client_manager import ClientManager

log = logging.getLogger("migrator.watcher")

CATCHUP_OVERLAP = 20  # re-check a few IDs below the watermark; duplicates are ignored


class Watcher:
    def __init__(self, clients: ClientManager, repo: Repository, store: SettingsStore, scanner: Scanner, notifier: Any) -> None:
        self.clients = clients
        self.repo = repo
        self.store = store
        self.scanner = scanner
        self.notifier = notifier
        self._routes: dict[str, list[Route]] = {}
        self._registered_on: TelegramClient | None = None
        self._poll_task: asyncio.Task | None = None
        self._catchup_lock = asyncio.Lock()
        self._sources: dict[str, SourceInfo] = {}

    @property
    def watched_count(self) -> int:
        return sum(len(routes) for routes in self._routes.values())

    async def start(self) -> None:
        reader = self.clients.reader
        if reader is None:
            return
        await self.refresh()
        if self._registered_on is not reader:
            reader.add_event_handler(self._on_message, events.NewMessage())
            reader.add_event_handler(self._on_album, events.Album())
            self._registered_on = reader
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop(), name="watch-poll")

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()

    async def refresh(self) -> None:
        routes = await self.repo.list_routes(watch_only=True)
        mapping: dict[str, list[Route]] = {}
        for route in routes:
            mapping.setdefault(str(route.source_chat_id), []).append(route)
        self._routes = mapping
        log.info("Watching %s route(s) across %s chat(s)", len(routes), len(mapping))

    # ---------------------------------------------------------------- events

    async def _on_message(self, event: Any) -> None:
        message = event.message
        if message.grouped_id:  # handled by the Album event
            return
        await self._handle(str(event.chat_id), [message])

    async def _on_album(self, event: Any) -> None:
        await self._handle(str(event.chat_id), sorted(event.messages, key=lambda m: m.id))

    async def _handle(self, chat_id: str, group: list[Any]) -> None:
        routes = self._routes.get(chat_id)
        if not routes or mh.media_kind(group[0]) == "unsupported":
            return
        for route in routes:
            if not self._topic_matches(route, group[0]):
                continue
            try:
                await self._enqueue(route, group)
                await self.repo.advance_watermark(route.id, max(int(m.id) for m in group))
            except Exception:
                log.exception("Route %s: failed to queue live message %s", route.name, group[0].id)

    @staticmethod
    def _topic_matches(route: Route, message: Any) -> bool:
        if not route.source_topic_id:
            return True
        topic = mh.topic_of(message)
        if route.source_topic_id == 1:
            return topic in (None, 1)
        return topic == route.source_topic_id

    async def _source(self, route: Route) -> SourceInfo:
        cached = self._sources.get(route.source_chat_id)
        if cached is None:
            cached = await self.scanner.resolve_source(route.source_chat_id)
            self._sources[route.source_chat_id] = cached
        return cached

    async def _enqueue(self, route: Route, group: list[Any]) -> EnqueueStats:
        targets = [t for t in [await self.repo.get_target(int(i)) for i in route.target_ids or []] if t and t.enabled]
        if not targets:
            return EnqueueStats()
        settings = effective_settings(self.store.current, route.overrides)
        return await self.scanner.enqueue_group(
            source=await self._source(route),
            group=group,
            targets=targets,
            settings=settings,
            content_filter=await self.scanner.content_filter(settings),
            route=route,
            topic_hint=route.source_topic_id,
        )

    # -------------------------------------------------------------- catch-up

    async def catch_up(self, route: Route) -> EnqueueStats:
        """Queue messages newer than the stored watermark (after restarts/gaps)."""

        stats = EnqueueStats()
        source = await self._source(route)
        reader = self.clients.require_reader()
        if route.last_seen_msg_id is None:
            latest = await self.scanner.limiter.call("read", reader.get_messages, source.entity, limit=1)
            latest_id = int(latest[0].id) if latest else 0
            await self.repo.advance_watermark(route.id, latest_id)
            log.info("Route %s starts watching after message %s", route.name, latest_id)
            return stats
        start = max(1, int(route.last_seen_msg_id) - CATCHUP_OVERLAP + 1)
        limit = self.store.current.watch.catchup_max_messages
        highest = int(route.last_seen_msg_id)
        async for group in self.scanner.iter_groups(source.entity, start, None, route.source_topic_id, limit=limit):
            if not self._topic_matches(route, group[0]):
                continue
            stats.add(await self._enqueue(route, group))
            highest = max(highest, max(int(m.id) for m in group))
        if highest > int(route.last_seen_msg_id):
            await self.repo.advance_watermark(route.id, highest)
        if stats.enqueued:
            log.info("Route %s catch-up queued %s job(s)", route.name, stats.enqueued)
        return stats

    async def catch_up_all(self) -> None:
        if self.clients.reader is None:
            return
        async with self._catchup_lock:
            for route in await self.repo.list_routes(watch_only=True):
                try:
                    await self.catch_up(route)
                except Exception as exc:
                    log.exception("Route %s catch-up failed", route.name)
                    lang = getattr(self.store.current.ui, "language", "en") or "en"
                    msg = (
                        f"⚠️ 监听路由 {route.name} 补齐失败：{exc.__class__.__name__}: {exc}"
                        if lang == "zh"
                        else f"⚠️ Watch route {route.name} catch-up failed: {exc.__class__.__name__}: {exc}"
                    )
                    await self.notifier.notify(msg, key=f"catchup:{route.id}")

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.catch_up_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Watch poll failed")
            await asyncio.sleep(self.store.current.watch.poll_interval_seconds)
