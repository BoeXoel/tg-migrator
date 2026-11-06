"""Route a job to the handler of its target type."""

from __future__ import annotations

import logging
from dataclasses import replace

from app.config import BootstrapConfig
from app.db.repository import Repository
from app.errors import TargetConfigError
from app.targets.base import DeliveryContext, DeliveryResult, TargetHandler
from app.targets.local_target import LocalTarget
from app.targets.telegram_target import TelegramTarget
from app.targets.webdav_target import WebDAVTarget
from app.telegram.client_manager import ClientManager
from app.telegram.limiter import TelegramLimiter

log = logging.getLogger("migrator.dispatch")


class TargetDispatcher:
    def __init__(self, boot: BootstrapConfig, clients: ClientManager, limiter: TelegramLimiter, repo: Repository) -> None:
        self.repo = repo
        self.handlers: dict[str, TargetHandler] = {
            "local": LocalTarget(boot, clients, limiter),
            "webdav": WebDAVTarget(boot, clients, limiter),
        }
        self.handlers["telegram"] = TelegramTarget(boot, clients, limiter, fallback=self._oversized_fallback)

    async def deliver(self, ctx: DeliveryContext) -> DeliveryResult:
        handler = self.handlers.get(ctx.target.type)
        if handler is None:
            raise TargetConfigError(f"Unknown target type {ctx.target.type!r}")
        return await handler.deliver(ctx)

    async def _oversized_fallback(self, ctx: DeliveryContext) -> DeliveryResult | None:
        fallback_id = ctx.target.fallback_target_id
        if not fallback_id:
            return None
        existing = await self.repo.remote_uri_for(ctx.job.source_chat_id, fallback_id, ctx.job.dedupe_key)
        if existing:
            return DeliveryResult(
                "done",
                transfer_route="fallback:reused",
                remote_uri=existing,
                reason="Oversized media was already archived to the fallback target",
                reason_code="oversized_fallback",
            )
        fallback = await self.repo.get_target(fallback_id)
        if fallback is None or not fallback.enabled or fallback.type not in {"local", "webdav"}:
            log.warning("Fallback target %s is missing, disabled or not an archive target", fallback_id)
            return None
        sub_ctx = replace(ctx, target=fallback, target_config=self.repo.decrypt_config(fallback.config))
        result = await self.handlers[fallback.type].deliver(sub_ctx)
        if result.status != "done":
            return result
        if result.remote_uri:
            await self.repo.record_remote_object(ctx.job.source_chat_id, fallback_id, ctx.job.dedupe_key, result.remote_uri)
        return replace(
            result,
            transfer_route=f"fallback:{fallback.type}",
            reason="Oversized for Telegram; archived to the fallback target",
            reason_code="oversized_fallback",
        )
