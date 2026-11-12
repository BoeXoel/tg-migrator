"""Background queue consumer."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from telethon import Button
from telethon.tl import types

from app.config import BootstrapConfig
from app.db.models import Job, utcnow
from app.db.repository import Repository
from app.engine.filters import ContentFilter, Rule
from app.engine.notifier import Notifier
from app.engine.storage_guard import StorageGuard
from app.errors import (
    AccountRestrictedError,
    DeferredJobError,
    PauseWorkerError,
    PeerUnresolvedError,
    PermanentJobError,
    ReaderUnavailableError,
    RetryableJobError,
    compact_error,
)
from app.settings_store import SettingsStore, effective_settings
from app.targets.base import DeliveryContext, DeliveryResult
from app.targets.dispatcher import TargetDispatcher
from app.telegram import media_helper as mh
from app.telegram.client_manager import ClientManager
from app.telegram.errors_map import (
    ACCOUNT_RESTRICTED_ERRORS,
    AUTH_LOST_ERRORS,
    BAD_REQUEST_ERRORS,
    FLOOD_WAIT_ERRORS,
    PEER_INVALID_ERRORS,
    PERMISSION_ERRORS,
    RETRYABLE_TELEGRAM_ERRORS,
    SOURCE_MISSING_ERRORS,
)
from app.telegram.limiter import TelegramLimiter

log = logging.getLogger("migrator.worker")


@dataclass
class ActiveJob:
    job_id: int
    target_name: str
    source: str
    phase: str
    started_at: datetime = field(default_factory=utcnow)


class Worker:
    def __init__(
        self,
        boot: BootstrapConfig,
        clients: ClientManager,
        limiter: TelegramLimiter,
        repo: Repository,
        store: SettingsStore,
        dispatcher: TargetDispatcher,
        notifier: Notifier,
    ) -> None:
        self.boot = boot
        self.clients = clients
        self.limiter = limiter
        self.repo = repo
        self.store = store
        self.dispatcher = dispatcher
        self.notifier = notifier
        self.wake_event = asyncio.Event()
        self.paused = False
        self.pause_reason: str | None = None
        self.active: dict[int, ActiveJob] = {}
        self.processed_since_pause = 0
        self.batch_pause_until: datetime | None = None
        self._stopping = False
        self._task: asyncio.Task | None = None

    # ---------------------------------------------------------------- control

    def wake(self) -> None:
        self.wake_event.set()

    async def load_state(self) -> None:
        self.paused = bool(await self.repo.get_state("worker_paused", False))
        self.pause_reason = await self.repo.get_state("worker_pause_reason", None)

    async def pause(self, reason: str = "Paused by admin") -> None:
        self.paused = True
        self.pause_reason = reason
        await self.repo.set_state("worker_paused", True)
        await self.repo.set_state("worker_pause_reason", reason)

    async def resume(self) -> None:
        self.paused = False
        self.pause_reason = None
        self.batch_pause_until = None
        await self.repo.set_state("worker_paused", False)
        await self.repo.set_state("worker_pause_reason", None)
        self.wake()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="worker")

    async def stop(self) -> None:
        self._stopping = True
        self.wake()
        if self._task:
            self._task.cancel()

    async def _sleep(self, seconds: float, *, wakeable: bool = True) -> None:
        if seconds <= 0:
            return
        if not wakeable:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(self.wake_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        self.wake_event.clear()

    # ------------------------------------------------------------------- loop

    async def run(self) -> None:
        recovered = await self.repo.recover_in_progress()
        if recovered:
            log.info("Recovered %s interrupted job(s)", recovered)
        guard = StorageGuard(self.boot.active_dir, self.boot.failed_dir, self.store.current.downloads)
        removed = guard.cleanup_active() + guard.prune_failed()
        if removed:
            log.info("Removed %s stale download directories", removed)

        while not self._stopping:
            settings = self.store.current
            if self.paused or self.clients.reader is None:
                await self._sleep(settings.worker.idle_sleep_seconds)
                continue
            if self.batch_pause_until and utcnow() < self.batch_pause_until:
                remaining = (self.batch_pause_until - utcnow()).total_seconds()
                await self._sleep(min(remaining, 60), wakeable=False)
                continue
            self.batch_pause_until = None

            batch_size = settings.worker.batch_size
            due = await self.repo.fetch_due_jobs(max(batch_size, settings.worker.concurrency * 4, 20))
            if not due:
                await self._sleep(await self._idle_timeout(settings.worker.idle_sleep_seconds))
                continue

            # Pick at most 1 job per (task_id, route_id, target_id) stream per wave:
            # different tasks/routes/targets execute concurrently, while jobs inside
            # the same stream execute strictly in chronological order.
            seen_streams: set[tuple[Any, Any, int]] = set()
            jobs: list[Job] = []
            for job in due:
                stream_key = (job.task_id, job.route_id, job.target_id)
                if stream_key not in seen_streams:
                    seen_streams.add(stream_key)
                    jobs.append(job)

            semaphore = asyncio.Semaphore(max(1, settings.worker.concurrency))

            async def run_one(job: Job) -> bool:
                async with semaphore:
                    if self.paused or self._stopping:
                        return False
                    return await self.process(job)

            results = await asyncio.gather(*(run_one(job) for job in jobs))
            self.processed_since_pause += sum(1 for item in results if item)
            if self.processed_since_pause >= batch_size:
                self.processed_since_pause = 0
                pause = settings.worker.pause_between_batches_seconds
                if pause > 0:
                    log.info("Batch of %s Telegram deliveries finished; pausing %ss (worker.pause_between_batches_seconds)", batch_size, pause)
                    self.batch_pause_until = utcnow() + timedelta(seconds=pause)

    async def _idle_timeout(self, default: int) -> float:
        next_retry = await self.repo.next_retry_time()
        if next_retry is None:
            return default
        return max(1.0, min(default, (next_retry - utcnow()).total_seconds()))

    # -------------------------------------------------------------- one job

    async def process(self, job: Job) -> bool:
        attempts = await self.repo.claim_job(job.id)
        if attempts is None:
            return False
        target = await self.repo.get_target(job.target_id)
        active = ActiveJob(job.id, target.name if target else "?", f"{job.source_title or job.source_chat_id}#{job.source_message_id}", "downloading")
        self.active[job.id] = active
        tg_delivered = False

        async def on_phase(phase: str) -> None:
            active.phase = phase
            await self.repo.update_job(job.id, status=phase)

        try:
            if target is None:
                await self.repo.mark_skipped(job.id, "Target was deleted", reason_code="target_missing")
                return False
            if not target.enabled:
                await self.repo.defer_job(job.id, "Target is disabled", reason_code="target_disabled", retry_after_seconds=3600)
                return False
            result = await self._deliver(job, target, on_phase)
            if result.status == "done":
                await self.repo.mark_done(
                    job.id,
                    dest_message_ids=result.dest_message_ids,
                    transfer_route=result.transfer_route or target.type,
                    remote_uri=result.remote_uri,
                    writer_identity=result.writer_identity,
                    upload_limit_bytes=result.upload_limit_bytes,
                    reason_code=result.reason_code,
                )
                log.info("Job %s -> %s done (%s)", job.id, target.name, result.transfer_route)
                tg_delivered = target.type == "telegram" and not str(result.transfer_route or "").startswith("fallback:")
            else:
                await self.repo.mark_skipped(
                    job.id, result.reason, reason_code=result.reason_code, transfer_route=result.transfer_route
                )
                if result.writer_identity:
                    await self.repo.update_job(
                        job.id, writer_identity=result.writer_identity, upload_limit_bytes=result.upload_limit_bytes
                    )
                log.info("Job %s skipped: %s", job.id, result.reason)
            return tg_delivered
        except Exception as exc:  # noqa: BLE001 - classified below
            await self._handle_error(job, attempts, exc)
            return False
        finally:
            self.active.pop(job.id, None)
            if job.task_id:
                await self._maybe_finish_task(job.task_id)

    async def _deliver(self, job: Job, target: Any, on_phase: Any) -> DeliveryResult:
        route = await self.repo.get_route(job.route_id) if job.route_id else None
        settings = effective_settings(self.store.current, route.overrides if route else None)
        reader = self.clients.require_reader()
        source_entity = await self.clients.resolve(reader, job.source_chat_id)
        loaded = await self.limiter.call("read", reader.get_messages, source_entity, ids=list(job.source_message_ids))
        if not isinstance(loaded, list):
            loaded = [loaded]
        messages = [m for m in loaded if m is not None and not isinstance(m, types.MessageService)]
        if not messages:
            return DeliveryResult("skipped", reason="Source message(s) no longer exist", reason_code="source_missing")

        rules = [Rule(r.id, r.rule_type, r.pattern) for r in await self.repo.list_filters(enabled_only=True)]
        content_filter = ContentFilter(rules, enabled=settings.filters.enabled, case_sensitive=settings.filters.case_sensitive)
        match = content_filter.match_texts(mh.message_text(m) for m in messages)
        if match:
            return DeliveryResult("skipped", reason=match.reason, reason_code=match.reason_code)

        include = settings.transfer.include
        messages = [m for m in messages if getattr(include, mh.include_flag(mh.media_kind(m)), False)]
        if not messages:
            return DeliveryResult("skipped", reason="All message types are disabled", reason_code="type_disabled")

        ctx = DeliveryContext(
            job=job,
            target=target,
            target_config=self.repo.decrypt_config(target.config),
            messages=messages,
            settings=settings,
            stop_event=_StopFlag(self),
            on_phase=on_phase,
            source_entity=source_entity,
            source_username=getattr(source_entity, "username", None),
        )
        return await self.dispatcher.deliver(ctx)

    async def _handle_error(self, job: Job, attempts: int, exc: BaseException) -> None:
        settings = self.store.current
        lang = getattr(settings.ui, "language", "en") or "en"
        error = compact_error(exc)

        if isinstance(exc, ACCOUNT_RESTRICTED_ERRORS):
            exc = AccountRestrictedError(
                "Telegram restricted the account (PeerFlood/UserRestricted)",
                retry_after_seconds=settings.limits.account_restricted_retry_seconds,
            )
        elif isinstance(exc, AUTH_LOST_ERRORS):
            await self.repo.defer_job(job.id, error, reason_code="auth_lost", retry_after_seconds=60)
            await self.pause(f"Telegram session invalid: {exc.__class__.__name__}")
            msg = (
                f"🛑 账号会话失效（{exc.__class__.__name__}），队列已暂停。请用 /login 重新登录后 /resume。"
                if lang == "zh"
                else f"🛑 Telegram session invalidated ({exc.__class__.__name__}); queue paused. Please /login again and then /resume."
            )
            await self.notifier.notify(msg, key="auth_lost")
            return
        elif isinstance(exc, PEER_INVALID_ERRORS):
            exc = PeerUnresolvedError(f"target of job {job.id}")

        if isinstance(exc, ReaderUnavailableError):
            await self.repo.defer_job(job.id, error, reason_code=exc.reason_code, retry_after_seconds=exc.retry_after_seconds)
            return
        if isinstance(exc, AccountRestrictedError):
            await self.repo.defer_job(job.id, compact_error(exc), reason_code=exc.reason_code, retry_after_seconds=exc.retry_after_seconds)
            await self.pause(str(exc))
            msg = (
                "🛑 Telegram 对账号施加了限制（PeerFlood/UserRestricted），队列已暂停，不会自动切换账号。"
                "请确认账号状态后发送 /resume。"
                if lang == "zh"
                else "🛑 Telegram restricted the account (PeerFlood/UserRestricted). Queue is paused. Verify account status before sending /resume."
            )
            await self.notifier.notify(msg, key="account_restricted")
            return
        if isinstance(exc, PeerUnresolvedError):
            await self.repo.defer_job(job.id, compact_error(exc), reason_code=exc.reason_code, retry_after_seconds=600)
            msg = (
                f"⚠️ 写入账号无法识别目标会话（任务 {job.id}）。请把 Bot 加入目标群/频道，并在其中发送 /bind。"
                if lang == "zh"
                else f"⚠️ Writer account cannot resolve target chat (job {job.id}). Please add the Bot to the target chat and send /bind there."
            )
            await self.notifier.notify(msg, key=f"peer:{job.target_id}")
            return
        if isinstance(exc, PauseWorkerError):
            await self.repo.defer_job(job.id, compact_error(exc), reason_code=exc.reason_code, retry_after_seconds=exc.retry_after_seconds)
            await self.pause(str(exc))
            msg = f"🛑 队列已暂停：{exc}" if lang == "zh" else f"🛑 Queue paused: {exc}"
            await self.notifier.notify(msg, key=f"pause:{exc.reason_code}")
            return
        if isinstance(exc, DeferredJobError):
            await self.repo.defer_job(job.id, error, reason_code=exc.reason_code, retry_after_seconds=exc.retry_after_seconds)
            log.warning("Job %s deferred %ss: %s", job.id, exc.retry_after_seconds, exc)
            if exc.reason_code in {"disk_low", "disk_full"}:
                msg = f"💾 磁盘空间不足，任务已推迟：{exc}" if lang == "zh" else f"💾 Low disk space; job deferred: {exc}"
                await self.notifier.notify(msg, key="disk")
            return
        if isinstance(exc, FLOOD_WAIT_ERRORS):
            raw_seconds = max(1, int(getattr(exc, "seconds", 60) or 60))
            low = min(settings.limits.floodwait_extra_min_seconds, settings.limits.floodwait_extra_max_seconds)
            high = max(settings.limits.floodwait_extra_min_seconds, settings.limits.floodwait_extra_max_seconds)
            wait = raw_seconds + random.randint(low, high)
            await self.repo.defer_job(job.id, error, reason_code="flood_wait", retry_after_seconds=wait)
            log.warning("Job %s deferred %ss due to Telegram flood wait: %s", job.id, wait, error)
            return
        if isinstance(exc, PermanentJobError):
            await self.repo.mark_skipped(job.id, error, reason_code=exc.reason_code or "permanent")
            log.warning("Job %s skipped: %s", job.id, exc)
            return
        if isinstance(exc, SOURCE_MISSING_ERRORS):
            await self.repo.mark_skipped(job.id, error, reason_code="source_missing")
            return
        if isinstance(exc, PERMISSION_ERRORS):
            await self.repo.mark_skipped(job.id, error, reason_code="permission_denied")
            msg = f"⚠️ 权限不足，任务 {job.id} 已跳过：{error}" if lang == "zh" else f"⚠️ Permission denied; job {job.id} skipped: {error}"
            await self.notifier.notify(msg, key=f"perm:{job.target_id}")
            return

        retryable = isinstance(exc, (RetryableJobError, ConnectionError, OSError, asyncio.TimeoutError) + RETRYABLE_TELEGRAM_ERRORS)
        if isinstance(exc, BAD_REQUEST_ERRORS) and not retryable:
            await self.repo.mark_skipped(job.id, error, reason_code="telegram_bad_request")
            log.warning("Job %s skipped by Telegram: %s", job.id, exc)
            return

        reason_code = getattr(exc, "reason_code", None) or ("network_error" if retryable else "unexpected_error")
        status = await self.repo.mark_failure(
            job.id,
            error,
            attempts=attempts,
            max_attempts=settings.queue.max_attempts,
            backoff=settings.queue.retry_backoff_seconds,
            reason_code=reason_code,
        )
        if retryable:
            log.warning("Job %s %s after error: %s", job.id, status, error)
        else:
            log.exception("Job %s %s after unexpected error", job.id, status, exc_info=exc)

    async def _maybe_finish_task(self, task_id: int) -> None:
        task = await self.repo.finish_task_if_complete(task_id)
        if task is None:
            return
        counts = await self.repo.task_summary_counts(task_id)
        done = counts["done"]
        filtered = counts["filtered"]
        blocked = counts["blocked"]
        failed = counts["failed"]
        problems = failed + blocked
        lang = getattr(self.store.current.ui, "language", "en") or "en"
        icon = "⚠️" if problems else "✅"
        if lang == "zh":
            title_suffix = "完成（存在异常项）" if problems else "完成"
            text = (
                f"{icon} 任务 #{task.id} {title_suffix}：{task.source_title or task.source_chat_id}\n"
                f"成功 {done} · 规则过滤 {filtered} · 异常阻断 {blocked} · 失败 {failed}"
            )
        else:
            title_suffix = "completed with issues" if problems else "completed"
            text = (
                f"{icon} Task #{task.id} {title_suffix}: {task.source_title or task.source_chat_id}\n"
                f"Done {done} · Filtered {filtered} · Blocked {blocked} · Failed {failed}"
            )

        buttons: list[list[Any]] | None = None
        if problems:
            buttons = [
                [
                    Button.inline(
                        f"⚠️ 异常明细 ({problems})" if lang == "zh" else f"⚠️ View Issues ({problems})",
                        f"err:t:{task.id}:0".encode(),
                    )
                ]
            ]
            retry_row: list[Any] = []
            if failed:
                retry_row.append(
                    Button.inline(
                        f"🔁 重试失败项 ({failed})" if lang == "zh" else f"🔁 Retry Failed ({failed})",
                        f"tk:r:{task.id}".encode(),
                    )
                )
            if blocked:
                label = (
                    (f"🔁 重试失败+阻断 ({problems})" if failed else f"🔁 重试阻断项 ({blocked})")
                    if lang == "zh"
                    else (f"🔁 Retry All ({problems})" if failed else f"🔁 Retry Blocked ({blocked})")
                )
                retry_row.append(Button.inline(label, f"tk:rb:{task.id}".encode()))
            if retry_row:
                buttons.append(retry_row)

        await self.notifier.notify(text, chat_id=task.notify_chat_id, buttons=buttons)

    def status_lines(self, lang: str = "en") -> list[str]:
        lines = []
        for item in self.active.values():
            lines.append(f"▶️ #{item.job_id} {item.source} → {item.target_name} ({item.phase})")
        if self.batch_pause_until:
            if lang == "zh":
                lines.append(f"⏸ 批次间歇至 {self.batch_pause_until:%H:%M:%S} UTC")
            else:
                lines.append(f"⏸ Batch cooldown until {self.batch_pause_until:%H:%M:%S} UTC")
        return lines


class _StopFlag:
    """asyncio.Event-like view of the worker stop state."""

    def __init__(self, worker: Worker) -> None:
        self._worker = worker

    def is_set(self) -> bool:
        return self._worker._stopping
