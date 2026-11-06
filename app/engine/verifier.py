"""Check that Telegram deliveries still exist in the destination chat."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.db.models import utcnow
from app.db.repository import Repository
from app.telegram.client_manager import ClientManager
from app.telegram.limiter import TelegramLimiter

log = logging.getLogger("migrator.verifier")


@dataclass
class VerifyStats:
    checked: int = 0
    verified: int = 0
    incomplete: int = 0
    errors: int = 0


class Verifier:
    def __init__(self, clients: ClientManager, limiter: TelegramLimiter, repo: Repository) -> None:
        self.clients = clients
        self.limiter = limiter
        self.repo = repo

    async def run(self, limit: int = 200) -> VerifyStats:
        stats = VerifyStats()
        writer = self.clients.writer
        for job in await self.repo.jobs_for_verification(limit):
            stats.checked += 1
            target = await self.repo.get_target(job.target_id)
            if target is None:
                continue
            try:
                chat = self.repo.decrypt_config(target.config).get("chat_id")
                entity = await self.clients.resolve(writer, chat)
                found = await self.limiter.call("verify", writer.get_messages, entity, ids=list(job.dest_message_ids or []))
                found = found if isinstance(found, list) else [found]
                present = [message for message in found if message is not None]
            except Exception as exc:
                stats.errors += 1
                log.warning("Verify job %s failed: %s", job.id, exc)
                continue
            if len(present) == len(job.dest_message_ids or []):
                await self.repo.update_job(job.id, verified_at=utcnow())
                stats.verified += 1
            else:
                stats.incomplete += 1
                log.warning("Job %s: %s/%s destination messages present", job.id, len(present), len(job.dest_message_ids or []))
        return stats
