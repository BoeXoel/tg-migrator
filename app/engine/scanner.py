"""History scanning: turn a message range into queued jobs."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from telethon import errors
from telethon.tl import types

from app.config import RuntimeSettings
from app.db.models import Route, Target, utcnow
from app.db.repository import Repository
from app.engine.filters import ContentFilter, Rule
from app.settings_store import SettingsStore, effective_settings
from app.targets.local_target import resolve_local_root
from app.targets.webdav_target import validate_webdav_url
from app.telegram import media_helper as mh
from app.telegram.client_manager import ClientManager, canonical_id, display_name, is_private_invite_link
from app.telegram.limiter import TelegramLimiter

log = logging.getLogger("migrator.scanner")

ALBUM_WINDOW = 10  # Telegram albums have at most 10 items


@dataclass
class SourceInfo:
    entity: Any
    chat_id: str
    title: str
    username: str | None


@dataclass
class EnqueueStats:
    groups: int = 0
    enqueued: int = 0
    skipped: int = 0
    duplicates: int = 0

    def add(self, other: "EnqueueStats") -> None:
        self.groups += other.groups
        self.enqueued += other.enqueued
        self.skipped += other.skipped
        self.duplicates += other.duplicates


class Scanner:
    def __init__(
        self,
        clients: ClientManager,
        limiter: TelegramLimiter,
        repo: Repository,
        store: SettingsStore,
        wake_worker: Callable[[], None],
    ) -> None:
        self.clients = clients
        self.limiter = limiter
        self.repo = repo
        self.store = store
        self.wake_worker = wake_worker

    # ------------------------------------------------------------- sources

    async def resolve_source(self, chat: str | int) -> SourceInfo:
        reader = self.clients.require_reader()
        entity = await self.clients.resolve(reader, chat)
        return SourceInfo(
            entity=entity,
            chat_id=canonical_id(entity),
            title=display_name(entity),
            username=getattr(entity, "username", None),
        )

    async def preflight_targets(self, target_ids: list[int]) -> list[Target]:
        """Verify targets before queuing anything; raises ValueError with a readable reason."""

        targets: list[Target] = []
        for target_id in target_ids:
            target = await self.repo.get_target(int(target_id))
            if target is None:
                raise ValueError(f"Target #{target_id} does not exist")
            if not target.enabled:
                raise ValueError(f"Target #{target.id} {target.name} is disabled")
            config = self.repo.decrypt_config(target.config)
            if target.type == "telegram":
                writer = self.clients.writer
                chat = config.get("chat_id")
                if writer is self.clients.bot and is_private_invite_link(chat):
                    raise ValueError(f"Target {target.name}: a Bot cannot use invite links; run /bind in that chat")
                try:
                    await self.clients.resolve(writer, chat)
                except Exception as exc:
                    raise ValueError(
                        f"Target {target.name}: the writer cannot see {chat} ({exc}). "
                        "Add the Bot to that chat and send /bind there."
                    ) from exc
            elif target.type == "local":
                resolve_local_root(self.clients.boot.archive_root, config.get("path"))
            elif target.type == "webdav":
                validate_webdav_url(str(config.get("url") or ""))
            targets.append(target)
        if not targets:
            raise ValueError("Choose at least one target")
        return targets

    async def content_filter(self, settings: RuntimeSettings) -> ContentFilter:
        rules = [Rule(rule.id, rule.rule_type, rule.pattern) for rule in await self.repo.list_filters(enabled_only=True)]
        return ContentFilter(rules, enabled=settings.filters.enabled, case_sensitive=settings.filters.case_sensitive)

    # ------------------------------------------------------------ iteration

    async def iter_messages(
        self, entity: Any, start: int, end: int | None, topic: int | None
    ) -> AsyncIterator[Any]:
        """Ascending messages in [start, end], resuming after FloodWaits."""

        reader = self.clients.require_reader()
        cursor = max(start - 1, 0)
        reply_to = topic if topic and topic != 1 else None
        while True:
            try:
                kwargs: dict[str, Any] = dict(
                    min_id=cursor,
                    reverse=True,
                    wait_time=self.store.current.limits.read_delay_seconds,
                )
                if end is not None:
                    kwargs["max_id"] = end + 1
                if reply_to:
                    kwargs["reply_to"] = reply_to
                async for message in reader.iter_messages(entity, **kwargs):
                    cursor = max(cursor, int(message.id))
                    yield message
                return
            except errors.FloodWaitError as exc:
                wait = int(exc.seconds) + random.randint(5, 20)
                log.warning("FloodWait %ss while scanning; resuming after message %s", wait, cursor)
                await asyncio.sleep(wait)

    async def iter_groups(
        self, entity: Any, start: int, end: int | None, topic: int | None, *, limit: int | None = None
    ) -> AsyncIterator[list[Any]]:
        buffer: list[Any] = []
        seen = 0
        first_group = True
        async for message in self.iter_messages(entity, start, end, topic):
            if isinstance(message, types.MessageService) or mh.media_kind(message) == "unsupported":
                continue
            if topic == 1 and mh.topic_of(message) not in (None, 1):
                continue  # "General" topic: only messages outside other topics
            if buffer and (message.grouped_id is None or message.grouped_id != buffer[0].grouped_id):
                yield await self._complete(entity, buffer, first_group)
                first_group = False
                buffer = []
            buffer.append(message)
            seen += 1
            if limit is not None and seen >= limit and message.grouped_id is None:
                break
        if buffer:
            yield await self._complete(entity, buffer, first_group, at_end=True)

    async def _complete(self, entity: Any, group: list[Any], first: bool, at_end: bool = False) -> list[Any]:
        """Fill in album items that lie just outside the scanned range."""

        if group[0].grouped_id is None or (not first and not at_end) or len(group) >= ALBUM_WINDOW:
            return group
        reader = self.clients.require_reader()
        low = max(1, min(m.id for m in group) - (ALBUM_WINDOW - 1))
        high = max(m.id for m in group) + (ALBUM_WINDOW - 1)
        around = await self.limiter.call("read", reader.get_messages, entity, ids=list(range(low, high + 1)))
        members = {m.id: m for m in group}
        for message in around or []:
            if message is not None and getattr(message, "grouped_id", None) == group[0].grouped_id:
                members.setdefault(message.id, message)
        return [members[key] for key in sorted(members)]

    # -------------------------------------------------------------- enqueue

    async def enqueue_group(
        self,
        *,
        source: SourceInfo,
        group: list[Any],
        targets: list[Target],
        settings: RuntimeSettings,
        content_filter: ContentFilter,
        task_id: int | None = None,
        route: Route | None = None,
        topic_hint: int | None = None,
    ) -> EnqueueStats:
        first = group[0]
        kinds = [mh.media_kind(message) for message in group]
        include = settings.transfer.include
        processable = any(getattr(include, mh.include_flag(kind), False) for kind in kinds)
        match = content_filter.match_texts(mh.message_text(message) for message in group)

        status, reason, reason_code = "pending", None, None
        if match is not None:
            status, reason, reason_code = "skipped", match.reason, match.reason_code
        elif not processable:
            status, reason, reason_code = "skipped", f"Message type disabled ({', '.join(sorted(set(kinds)))})", "type_disabled"
        stats = EnqueueStats(groups=1)
        if status == "skipped" and not settings.queue.record_skipped:
            stats.skipped += 1
            return stats

        caption = mh.group_caption(group)
        topic = mh.topic_of(first) or topic_hint
        common = dict(
            task_id=task_id,
            route_id=route.id if route else None,
            source_chat_id=source.chat_id,
            source_title=source.title[:255],
            source_topic_id=topic,
            source_message_id=int(first.id),
            source_message_ids=[int(message.id) for message in group],
            grouped_id=str(first.grouped_id) if first.grouped_id else None,
            dedupe_key=mh.dedupe_key(source.chat_id, group),
            media_type=mh.group_media_type(group),
            file_size=mh.group_file_size(group),
            caption=caption[:1000] if caption else None,
            message_date=mh.message_date(first),
            media_manifest=mh.media_manifest(group),
            status=status,
            last_error=reason,
            reason_code=reason_code,
        )
        rows = [{**common, "target_id": target.id} for target in targets]
        inserted = await self.repo.enqueue_jobs(rows)
        if status == "pending":
            stats.enqueued += inserted
        else:
            stats.skipped += inserted
        stats.duplicates += len(rows) - inserted
        if inserted and status == "pending":
            self.wake_worker()
        return stats

    # ----------------------------------------------------------------- tasks

    async def run_task(self, task_id: int) -> EnqueueStats:
        """Scan the range of a task and queue its jobs (runs in the background)."""

        task = await self.repo.get_task(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")
        route = await self.repo.get_route(task.route_id) if task.route_id else None
        settings = effective_settings(self.store.current, route.overrides if route else None)
        stats = EnqueueStats()
        try:
            targets = await self.preflight_targets([int(item) for item in task.target_ids or []])
            source = await self.resolve_source(task.source_chat_id)
            await self.repo.update_task(task_id, source_chat_id=source.chat_id, source_title=source.title[:255])
            content_filter = await self.content_filter(settings)
            start = int(task.start_id or 1)
            end = int(task.end_id) if task.end_id is not None else None
            last_id = 0
            async for group in self.iter_groups(source.entity, start, end, task.source_topic_id):
                stats.add(
                    await self.enqueue_group(
                        source=source,
                        group=group,
                        targets=targets,
                        settings=settings,
                        content_filter=content_filter,
                        task_id=task_id,
                        route=route,
                        topic_hint=task.source_topic_id,
                    )
                )
                last_id = max(last_id, max(int(m.id) for m in group))
                if stats.groups % 50 == 0:
                    await self._save_stats(task_id, stats, "scanning")
            if route is not None and route.watch_enabled and last_id:
                await self.repo.advance_watermark(route.id, last_id)
            await self._save_stats(task_id, stats, "queued")
            log.info(
                "Task %s scanned: groups=%s queued=%s skipped=%s duplicates=%s",
                task_id, stats.groups, stats.enqueued, stats.skipped, stats.duplicates,
            )
            return stats
        except asyncio.CancelledError:
            await self._save_stats(task_id, stats, "cancelled")
            await self.repo.cancel_task(task_id)
            raise
        except Exception as exc:
            await self.repo.update_task(task_id, status="error", error=str(exc)[:2000], finished_at=utcnow())
            raise

    async def _save_stats(self, task_id: int, stats: EnqueueStats, status: str) -> None:
        await self.repo.update_task(
            task_id,
            status=status,
            scanned_groups=stats.groups,
            enqueued=stats.enqueued,
            duplicates=stats.duplicates,
            skipped_at_scan=stats.skipped,
        )
