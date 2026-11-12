"""Data access layer. Every method opens its own short transaction."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Iterable, Sequence

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.config import SECTION_NAMES, RuntimeSettings, dataclass_to_dict, settings_from_sections
from app.db.models import (
    BENIGN_SKIP_REASONS,
    TERMINAL_STATUSES,
    FilterRule,
    Job,
    RemoteObject,
    Route,
    Setting,
    Target,
    Task,
    utcnow,
)
from app.security import SecretBox

log = logging.getLogger("migrator.db")

SECRET_CONFIG_KEYS = ("password",)


class Repository:
    def __init__(self, engine: AsyncEngine, sessionmaker: async_sessionmaker, secrets: SecretBox) -> None:
        self.engine = engine
        self._sm = sessionmaker
        self.secrets = secrets
        self.dialect = engine.dialect.name

    # ------------------------------------------------------------------ utils

    def _insert_ignore(self, model: Any, values: dict[str, Any]):
        table = model.__table__
        if self.dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert

            return insert(table).values(**values).on_conflict_do_nothing()
        if self.dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert

            return insert(table).values(**values).on_conflict_do_nothing()
        if self.dialect in {"mysql", "mariadb"}:
            from sqlalchemy.dialects.mysql import insert

            return insert(table).values(**values).prefix_with("IGNORE")
        raise RuntimeError(f"Unsupported dialect {self.dialect}")

    # --------------------------------------------------------------- settings

    async def load_settings(self, *, default_language: str = "en") -> RuntimeSettings:
        async with self._sm() as session:
            rows = (await session.execute(select(Setting).where(Setting.key.in_(SECTION_NAMES)))).scalars().all()
        sections: dict[str, Any] = {}
        for row in rows:
            if isinstance(row.value, dict):
                sections[row.key] = row.value
        if "ui" not in sections:
            sections["ui"] = {"language": default_language}
        try:
            return settings_from_sections(sections)
        except (TypeError, ValueError) as exc:
            log.error("Stored settings are invalid (%s); falling back to defaults per section", exc)
            valid: dict[str, Any] = {}
            for key, value in sections.items():
                try:
                    settings_from_sections({key: value})
                    valid[key] = value
                except (TypeError, ValueError):
                    continue
            return settings_from_sections(valid)

    async def save_settings(self, settings: RuntimeSettings) -> None:
        data = dataclass_to_dict(settings)
        async with self._sm() as session:
            for key, value in data.items():
                row = await session.get(Setting, key)
                if row is None:
                    session.add(Setting(key=key, value=value))
                else:
                    row.value = value
            await session.commit()

    async def get_state(self, name: str, default: Any = None) -> Any:
        async with self._sm() as session:
            row = await session.get(Setting, f"state.{name}")
            return default if row is None else row.value

    async def set_state(self, name: str, value: Any) -> None:
        async with self._sm() as session:
            row = await session.get(Setting, f"state.{name}")
            if row is None:
                session.add(Setting(key=f"state.{name}", value=value))
            else:
                row.value = value
            await session.commit()

    # ---------------------------------------------------------------- targets

    def encrypt_config(self, config: dict[str, Any]) -> dict[str, Any]:
        result = dict(config)
        for key in SECRET_CONFIG_KEYS:
            if result.get(key):
                result[key] = self.secrets.encrypt(str(result[key]))
        return result

    def decrypt_config(self, config: dict[str, Any] | None) -> dict[str, Any]:
        result = dict(config or {})
        for key in SECRET_CONFIG_KEYS:
            if result.get(key):
                result[key] = self.secrets.decrypt(str(result[key]))
        return result

    async def list_targets(self, *, enabled_only: bool = False) -> list[Target]:
        async with self._sm() as session:
            stmt = select(Target).order_by(Target.id)
            if enabled_only:
                stmt = stmt.where(Target.enabled.is_(True))
            return list((await session.execute(stmt)).scalars().all())

    async def get_target(self, target_id: int) -> Target | None:
        async with self._sm() as session:
            return await session.get(Target, target_id)

    async def get_target_by_name(self, name: str) -> Target | None:
        async with self._sm() as session:
            return (await session.execute(select(Target).where(Target.name == name))).scalar_one_or_none()

    async def find_telegram_target(self, chat_id: str, topic_id: int | None) -> Target | None:
        for target in await self.list_targets():
            config = target.config or {}
            if target.type == "telegram" and str(config.get("chat_id")) == str(chat_id) and (
                config.get("topic_id") or None
            ) == (topic_id or None):
                return target
        return None

    async def add_target(
        self,
        *,
        name: str,
        type: str,
        config: dict[str, Any],
        enabled: bool = True,
        fallback_target_id: int | None = None,
    ) -> Target:
        async with self._sm() as session:
            target = Target(
                name=name,
                type=type,
                config=self.encrypt_config(config),
                enabled=enabled,
                fallback_target_id=fallback_target_id,
            )
            session.add(target)
            await session.commit()
            return target

    async def update_target(self, target_id: int, **values: Any) -> Target | None:
        async with self._sm() as session:
            target = await session.get(Target, target_id)
            if target is None:
                return None
            for key, value in values.items():
                if key == "config":
                    value = self.encrypt_config(value)
                setattr(target, key, value)
            await session.commit()
            return target

    async def delete_target(self, target_id: int) -> bool:
        async with self._sm() as session:
            target = await session.get(Target, target_id)
            if target is None:
                return False
            # Detach from routes and fallbacks first (JSON lists have no FK).
            routes = (await session.execute(select(Route))).scalars().all()
            for route in routes:
                ids = [int(item) for item in (route.target_ids or []) if int(item) != target_id]
                if ids != list(route.target_ids or []):
                    route.target_ids = ids
            await session.execute(
                update(Target).where(Target.fallback_target_id == target_id).values(fallback_target_id=None)
            )
            await session.execute(delete(Job).where(Job.target_id == target_id))
            await session.delete(target)
            await session.commit()
            return True

    # ----------------------------------------------------------------- routes

    async def list_routes(self, *, watch_only: bool = False) -> list[Route]:
        async with self._sm() as session:
            stmt = select(Route).order_by(Route.id)
            if watch_only:
                stmt = stmt.where(Route.watch_enabled.is_(True), Route.enabled.is_(True))
            return list((await session.execute(stmt)).scalars().all())

    async def get_route(self, route_id: int) -> Route | None:
        async with self._sm() as session:
            return await session.get(Route, route_id)

    async def get_route_by_name(self, name: str) -> Route | None:
        async with self._sm() as session:
            return (await session.execute(select(Route).where(Route.name == name))).scalar_one_or_none()

    async def add_route(self, **values: Any) -> Route:
        async with self._sm() as session:
            route = Route(**values)
            session.add(route)
            await session.commit()
            return route

    async def update_route(self, route_id: int, **values: Any) -> Route | None:
        async with self._sm() as session:
            route = await session.get(Route, route_id)
            if route is None:
                return None
            for key, value in values.items():
                setattr(route, key, value)
            await session.commit()
            return route

    async def delete_route(self, route_id: int) -> bool:
        async with self._sm() as session:
            route = await session.get(Route, route_id)
            if route is None:
                return False
            await session.delete(route)
            await session.commit()
            return True

    async def advance_watermark(self, route_id: int, message_id: int) -> None:
        """Move the route watermark forward only (never backwards)."""

        async with self._sm() as session:
            await session.execute(
                update(Route)
                .where(
                    Route.id == route_id,
                    or_(Route.last_seen_msg_id.is_(None), Route.last_seen_msg_id < message_id),
                )
                .values(last_seen_msg_id=message_id, updated_at=utcnow())
            )
            await session.commit()

    # ---------------------------------------------------------------- filters

    async def list_filters(self, *, enabled_only: bool = False) -> list[FilterRule]:
        async with self._sm() as session:
            stmt = select(FilterRule).order_by(FilterRule.id)
            if enabled_only:
                stmt = stmt.where(FilterRule.enabled.is_(True))
            return list((await session.execute(stmt)).scalars().all())

    async def add_filter(self, rule_type: str, pattern: str, enabled: bool = True) -> FilterRule:
        async with self._sm() as session:
            rule = FilterRule(rule_type=rule_type, pattern=pattern, enabled=enabled)
            session.add(rule)
            await session.commit()
            return rule

    async def delete_filter(self, rule_id: int) -> bool:
        async with self._sm() as session:
            result = await session.execute(delete(FilterRule).where(FilterRule.id == rule_id))
            await session.commit()
            return bool(result.rowcount)

    async def set_filter_enabled(self, rule_id: int, enabled: bool) -> bool:
        async with self._sm() as session:
            result = await session.execute(update(FilterRule).where(FilterRule.id == rule_id).values(enabled=enabled))
            await session.commit()
            return bool(result.rowcount)

    async def replace_filters(self, rules: Iterable[tuple[str, str, bool]]) -> None:
        async with self._sm() as session:
            await session.execute(delete(FilterRule))
            for rule_type, pattern, enabled in rules:
                session.add(FilterRule(rule_type=rule_type, pattern=pattern, enabled=enabled))
            await session.commit()

    # ------------------------------------------------------------------ tasks

    async def create_task(self, **values: Any) -> Task:
        async with self._sm() as session:
            task = Task(**values)
            session.add(task)
            await session.commit()
            return task

    async def update_task(self, task_id: int, **values: Any) -> None:
        async with self._sm() as session:
            await session.execute(update(Task).where(Task.id == task_id).values(updated_at=utcnow(), **values))
            await session.commit()

    async def get_task(self, task_id: int) -> Task | None:
        async with self._sm() as session:
            return await session.get(Task, task_id)

    async def list_tasks(self, limit: int = 100) -> list[Task]:
        async with self._sm() as session:
            stmt = select(Task).order_by(Task.id.desc()).limit(limit)
            return list((await session.execute(stmt)).scalars().all())

    async def task_job_counts(self, task_id: int) -> dict[str, int]:
        async with self._sm() as session:
            rows = await session.execute(
                select(Job.status, func.count()).where(Job.task_id == task_id).group_by(Job.status)
            )
            return {str(status): int(count) for status, count in rows.all()}

    async def task_summary_counts(self, task_id: int) -> dict[str, int]:
        """Return detailed job counts splitting skipped into ``filtered`` vs ``blocked``."""

        async with self._sm() as session:
            rows = await session.execute(
                select(Job.status, Job.reason_code, func.count())
                .where(Job.task_id == task_id)
                .group_by(Job.status, Job.reason_code)
            )
            done = failed = filtered = blocked = open_jobs = 0
            for status, reason_code, count in rows.all():
                n = int(count)
                if status == "done":
                    done += n
                elif status == "failed":
                    failed += n
                elif status == "skipped":
                    if reason_code in BENIGN_SKIP_REASONS:
                        filtered += n
                    else:
                        blocked += n
                else:
                    open_jobs += n
            return {
                "done": done,
                "failed": failed,
                "filtered": filtered,
                "blocked": blocked,
                "skipped": filtered + blocked,
                "open": open_jobs,
            }

    async def finish_task_if_complete(self, task_id: int) -> Task | None:
        """Mark a queued task done when it has no unfinished jobs; return it once."""

        async with self._sm() as session:
            task = await session.get(Task, task_id)
            if task is None or task.status != "queued":
                return None
            open_jobs = await session.scalar(
                select(func.count()).select_from(Job).where(
                    Job.task_id == task_id, Job.status.not_in(TERMINAL_STATUSES)
                )
            )
            if open_jobs:
                return None
            result = await session.execute(
                update(Task)
                .where(Task.id == task_id, Task.status == "queued")
                .values(status="done", finished_at=utcnow(), updated_at=utcnow())
            )
            await session.commit()
            if not result.rowcount:
                return None
            await session.refresh(task)
            return task

    async def cancel_task(self, task_id: int) -> int:
        async with self._sm() as session:
            result = await session.execute(
                update(Job)
                .where(Job.task_id == task_id, Job.status == "pending")
                .values(status="skipped", reason_code="cancelled", last_error="Cancelled by admin", updated_at=utcnow())
            )
            await session.execute(
                update(Task).where(Task.id == task_id).values(status="cancelled", finished_at=utcnow())
            )
            await session.commit()
            return int(result.rowcount or 0)

    # ------------------------------------------------------------------- jobs

    async def enqueue_jobs(self, rows: Sequence[dict[str, Any]]) -> int:
        """Insert jobs, ignoring duplicates. Returns the number of new rows."""

        if not rows:
            return 0
        inserted = 0
        now = utcnow()
        async with self._sm() as session:
            for values in rows:
                payload = {"created_at": now, "updated_at": now, "attempts": 0, **values}
                result = await session.execute(self._insert_ignore(Job, payload))
                inserted += int(result.rowcount or 0)
            await session.commit()
        return inserted

    async def get_job(self, job_id: int) -> Job | None:
        async with self._sm() as session:
            return await session.get(Job, job_id)

    async def fetch_due_jobs(self, limit: int, *, reason_code: str | None = None) -> list[Job]:
        now = utcnow()
        async with self._sm() as session:
            rn = (
                func.row_number()
                .over(
                    partition_by=(Job.task_id, Job.route_id, Job.target_id),
                    order_by=Job.id,
                )
                .label("rn")
            )
            is_task = case((Job.task_id.is_(None), 0), else_=1).label("is_task")
            subq = select(Job.id.label("job_id"), rn, is_task).where(
                Job.status == "pending",
                or_(Job.next_retry_at.is_(None), Job.next_retry_at <= now),
            )
            if reason_code is not None:
                subq = subq.where(Job.reason_code == reason_code)
            sub = subq.subquery()
            stmt = (
                select(Job)
                .join(sub, Job.id == sub.c.job_id)
                .order_by(sub.c.rn, sub.c.is_task, Job.id)
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def next_retry_time(self):
        async with self._sm() as session:
            return await session.scalar(
                select(func.min(Job.next_retry_at)).where(Job.status == "pending", Job.next_retry_at.is_not(None))
            )

    async def claim_job(self, job_id: int) -> int | None:
        """Atomically move a pending job to ``downloading``; returns the attempt number."""

        async with self._sm() as session:
            result = await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "pending")
                .values(status="downloading", attempts=Job.attempts + 1, updated_at=utcnow())
            )
            if not result.rowcount:
                await session.rollback()
                return None
            attempts = await session.scalar(select(Job.attempts).where(Job.id == job_id))
            await session.commit()
            return int(attempts or 1)

    async def update_job(self, job_id: int, **values: Any) -> None:
        async with self._sm() as session:
            await session.execute(update(Job).where(Job.id == job_id).values(updated_at=utcnow(), **values))
            await session.commit()

    async def mark_done(
        self,
        job_id: int,
        *,
        dest_message_ids: list[int] | None,
        transfer_route: str,
        remote_uri: str | None = None,
        writer_identity: str | None = None,
        upload_limit_bytes: int | None = None,
        reason_code: str | None = None,
    ) -> None:
        values: dict[str, Any] = dict(
            status="done",
            dest_message_ids=dest_message_ids,
            transfer_route=transfer_route,
            remote_uri=remote_uri,
            reason_code=reason_code,
            last_error=None,
            next_retry_at=None,
        )
        if writer_identity:
            values["writer_identity"] = writer_identity
            values["upload_limit_bytes"] = upload_limit_bytes
        await self.update_job(job_id, **values)

    async def mark_skipped(
        self, job_id: int, reason: str, *, reason_code: str | None, transfer_route: str | None = None
    ) -> None:
        await self.update_job(
            job_id,
            status="skipped",
            last_error=reason[:2000],
            reason_code=reason_code,
            transfer_route=transfer_route,
            next_retry_at=None,
        )

    async def defer_job(self, job_id: int, error: str, *, reason_code: str | None, retry_after_seconds: int) -> None:
        async with self._sm() as session:
            await session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status="pending",
                    attempts=case((Job.attempts > 0, Job.attempts - 1), else_=0),
                    last_error=error[:2000],
                    reason_code=reason_code,
                    next_retry_at=utcnow() + timedelta(seconds=retry_after_seconds),
                    updated_at=utcnow(),
                )
            )
            await session.commit()

    async def mark_failure(
        self,
        job_id: int,
        error: str,
        *,
        attempts: int,
        max_attempts: int,
        backoff: list[int],
        reason_code: str | None,
    ) -> str:
        if attempts >= max_attempts:
            await self.update_job(job_id, status="failed", last_error=error[:2000], reason_code=reason_code, next_retry_at=None)
            return "failed"
        delay = backoff[min(max(attempts - 1, 0), len(backoff) - 1)] if backoff else 300
        await self.update_job(
            job_id,
            status="pending",
            last_error=error[:2000],
            reason_code=reason_code,
            next_retry_at=utcnow() + timedelta(seconds=delay),
        )
        return "pending"

    async def recover_in_progress(self) -> int:
        async with self._sm() as session:
            result = await session.execute(
                update(Job)
                .where(Job.status.in_(("downloading", "uploading")))
                .values(status="pending", last_error="Recovered after an interrupted run", updated_at=utcnow())
            )
            await session.commit()
            return int(result.rowcount or 0)

    @staticmethod
    def _blocked_skip_clause() -> Any:
        return and_(
            Job.status == "skipped",
            or_(Job.reason_code.is_(None), Job.reason_code.not_in(BENIGN_SKIP_REASONS)),
        )

    @classmethod
    def _problem_job_clause(cls) -> Any:
        return or_(Job.status == "failed", cls._blocked_skip_clause())

    async def retry_jobs(
        self,
        *,
        task_id: int | None = None,
        include_blocked: bool = False,
        statuses: tuple[str, ...] = ("failed",),
    ) -> int:
        cond = or_(Job.status.in_(statuses), self._blocked_skip_clause()) if include_blocked else Job.status.in_(statuses)
        async with self._sm() as session:
            stmt = (
                update(Job)
                .where(cond)
                .values(status="pending", attempts=0, next_retry_at=None, updated_at=utcnow())
            )
            if task_id is not None:
                stmt = stmt.where(Job.task_id == task_id)
            result = await session.execute(stmt)
            requeued = int(result.rowcount or 0)
            if requeued:
                if task_id is not None:
                    await session.execute(
                        update(Task).where(Task.id == task_id, Task.status == "done").values(status="queued", finished_at=None)
                    )
                else:
                    pending_task_ids = select(Job.task_id).where(Job.status == "pending", Job.task_id.is_not(None)).distinct()
                    await session.execute(
                        update(Task)
                        .where(Task.id.in_(pending_task_ids), Task.status == "done")
                        .values(status="queued", finished_at=None)
                    )
            await session.commit()
            return requeued

    async def counts_by_status(self) -> dict[str, int]:
        async with self._sm() as session:
            rows = await session.execute(select(Job.status, func.count()).group_by(Job.status))
            return {str(status): int(count) for status, count in rows.all()}

    async def count_problem_jobs(self, *, task_id: int | None = None) -> dict[str, int]:
        async with self._sm() as session:
            stmt = select(Job.status, func.count()).where(self._problem_job_clause()).group_by(Job.status)
            if task_id is not None:
                stmt = stmt.where(Job.task_id == task_id)
            rows = {str(status): int(count) for status, count in (await session.execute(stmt)).all()}
            failed = rows.get("failed", 0)
            blocked = rows.get("skipped", 0)
            return {"failed": failed, "blocked": blocked, "total": failed + blocked}

    async def list_problem_jobs(self, *, task_id: int | None = None, limit: int = 200) -> list[Job]:
        async with self._sm() as session:
            stmt = (
                select(Job)
                .where(self._problem_job_clause())
                .order_by(Job.updated_at.desc(), Job.id.desc())
                .limit(limit)
            )
            if task_id is not None:
                stmt = stmt.where(Job.task_id == task_id)
            return list((await session.execute(stmt)).scalars().all())

    async def report_jobs(self, *, task_id: int | None = None) -> list[Job]:
        """Return all failed, blocked-skipped, and oversized-fallback jobs for CSV export."""

        async with self._sm() as session:
            stmt = (
                select(Job)
                .where(or_(self._problem_job_clause(), Job.reason_code.in_(("oversized", "oversized_fallback"))))
                .order_by(Job.id.desc())
            )
            if task_id is not None:
                stmt = stmt.where(Job.task_id == task_id)
            return list((await session.execute(stmt)).scalars().all())

    async def jobs_for_verification(self, limit: int) -> list[Job]:
        async with self._sm() as session:
            stmt = (
                select(Job)
                .join(Target, Target.id == Job.target_id)
                .where(
                    Job.status == "done",
                    Job.verified_at.is_(None),
                    Job.dest_message_ids.is_not(None),
                    Target.type == "telegram",
                )
                .order_by(Job.id)
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def oversized_jobs(self) -> list[Job]:
        async with self._sm() as session:
            stmt = (
                select(Job)
                .where(Job.reason_code.in_(("oversized", "oversized_fallback")))
                .order_by(Job.source_chat_id, Job.source_message_id)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def recent_problem_jobs(self, limit: int = 10) -> list[Job]:
        async with self._sm() as session:
            stmt = (
                select(Job)
                .where(or_(Job.status == "failed", and_(Job.status == "pending", Job.last_error.is_not(None))))
                .order_by(Job.updated_at.desc())
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    # --------------------------------------------------------- remote objects

    async def remote_uri_for(self, source_chat_id: str, target_id: int, dedupe_key: str) -> str | None:
        async with self._sm() as session:
            row = await session.get(RemoteObject, (source_chat_id, target_id, dedupe_key))
            return row.remote_uri if row else None

    async def record_remote_object(self, source_chat_id: str, target_id: int, dedupe_key: str, remote_uri: str) -> None:
        async with self._sm() as session:
            row = await session.get(RemoteObject, (source_chat_id, target_id, dedupe_key))
            if row is None:
                session.add(
                    RemoteObject(
                        source_chat_id=source_chat_id, target_id=target_id, dedupe_key=dedupe_key, remote_uri=remote_uri
                    )
                )
            else:
                row.remote_uri = remote_uri
            await session.commit()
