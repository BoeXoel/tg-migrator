from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from app.config import RuntimeSettings, set_setting
from app.db.engine import create_engine, init_models, make_sessionmaker, normalise_database_url
from app.db.models import utcnow
from app.db.repository import Repository
from app.security import SecretBox, load_or_create_key


def run(coro):
    return asyncio.run(coro)


async def make_repo(tmp_path: Path) -> Repository:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await init_models(engine)
    return Repository(engine, make_sessionmaker(engine), SecretBox(load_or_create_key("", tmp_path)))


def job_row(target_id: int, key: str = "k1", **values):
    row = dict(
        target_id=target_id,
        source_chat_id="-1001",
        source_title="Src",
        source_message_id=1,
        source_message_ids=[1],
        dedupe_key=key,
        media_type="text",
        status="pending",
    )
    row.update(values)
    return row


def test_database_url_normalisation():
    assert normalise_database_url("postgresql://u:p@h/db").startswith("postgresql+asyncpg://")
    assert "charset=utf8mb4" in normalise_database_url("mysql://u:p@h/db")
    with pytest.raises(ValueError):
        normalise_database_url("oracle://u:p@h/db")


def test_settings_roundtrip(tmp_path):
    async def scenario():
        repo = await make_repo(tmp_path)
        settings = set_setting(RuntimeSettings(), "worker.batch_size", 7)
        await repo.save_settings(settings)
        loaded = await repo.load_settings()
        await repo.set_state("worker_paused", True)
        return loaded, await repo.get_state("worker_paused")

    loaded, paused = run(scenario())
    assert loaded.worker.batch_size == 7 and paused is True


def test_target_secret_encrypted_at_rest(tmp_path):
    async def scenario():
        repo = await make_repo(tmp_path)
        target = await repo.add_target(name="dav", type="webdav", config={"url": "https://x", "password": "s3cret"})
        stored = (await repo.get_target(target.id)).config
        return stored, repo.decrypt_config(stored)

    stored, plain = run(scenario())
    assert stored["password"].startswith("enc:v1:") and "s3cret" not in stored["password"]
    assert plain["password"] == "s3cret"


def test_enqueue_dedupe_claim_and_failure(tmp_path):
    async def scenario():
        repo = await make_repo(tmp_path)
        target = await repo.add_target(name="t", type="local", config={})
        first = await repo.enqueue_jobs([job_row(target.id)])
        second = await repo.enqueue_jobs([job_row(target.id)])
        job = (await repo.fetch_due_jobs(10))[0]
        attempt = await repo.claim_job(job.id)
        again = await repo.claim_job(job.id)
        status = await repo.mark_failure(job.id, "boom", attempts=attempt, max_attempts=2, backoff=[60], reason_code="network_error")
        due_now = await repo.fetch_due_jobs(10)
        await repo.update_job(job.id, next_retry_at=utcnow() - timedelta(seconds=1))
        attempt2 = await repo.claim_job(job.id)
        final = await repo.mark_failure(job.id, "boom", attempts=attempt2, max_attempts=2, backoff=[60], reason_code="network_error")
        return first, second, attempt, again, status, due_now, attempt2, final

    first, second, attempt, again, status, due_now, attempt2, final = run(scenario())
    assert (first, second) == (1, 0)
    assert attempt == 1 and again is None
    assert status == "pending" and due_now == []
    assert attempt2 == 2 and final == "failed"


def test_defer_does_not_consume_attempt(tmp_path):
    async def scenario():
        repo = await make_repo(tmp_path)
        target = await repo.add_target(name="t", type="local", config={})
        await repo.enqueue_jobs([job_row(target.id)])
        job = (await repo.fetch_due_jobs(1))[0]
        await repo.claim_job(job.id)
        await repo.defer_job(job.id, "flood", reason_code="flood_wait", retry_after_seconds=30)
        return await repo.get_job(job.id)

    job = run(scenario())
    assert job.attempts == 0 and job.status == "pending" and job.reason_code == "flood_wait"


def test_task_completion_and_recover(tmp_path):
    async def scenario():
        repo = await make_repo(tmp_path)
        target = await repo.add_target(name="t", type="local", config={})
        task = await repo.create_task(kind="adhoc", source_chat_id="-1001", target_ids=[target.id], status="queued")
        await repo.enqueue_jobs([job_row(target.id, "a", task_id=task.id), job_row(target.id, "b", task_id=task.id)])
        jobs = await repo.fetch_due_jobs(10)
        await repo.claim_job(jobs[0].id)
        recovered = await repo.recover_in_progress()
        not_yet = await repo.finish_task_if_complete(task.id)
        for job in jobs:
            await repo.mark_done(job.id, dest_message_ids=[1], transfer_route="local")
        finished = await repo.finish_task_if_complete(task.id)
        twice = await repo.finish_task_if_complete(task.id)
        return recovered, not_yet, finished, twice

    recovered, not_yet, finished, twice = run(scenario())
    assert recovered == 1 and not_yet is None
    assert finished is not None and finished.status == "done" and twice is None


def test_watermark_only_moves_forward(tmp_path):
    async def scenario():
        repo = await make_repo(tmp_path)
        route = await repo.add_route(name="r", source_chat_id="-1001", target_ids=[])
        await repo.advance_watermark(route.id, 50)
        await repo.advance_watermark(route.id, 20)
        return (await repo.get_route(route.id)).last_seen_msg_id

    assert run(scenario()) == 50


def test_delete_target_detaches_routes(tmp_path):
    async def scenario():
        repo = await make_repo(tmp_path)
        a = await repo.add_target(name="a", type="local", config={})
        b = await repo.add_target(name="b", type="local", config={})
        tg = await repo.add_target(name="tg", type="telegram", config={"chat_id": "-1002"}, fallback_target_id=a.id)
        route = await repo.add_route(name="r", source_chat_id="-1001", target_ids=[a.id, b.id])
        await repo.enqueue_jobs([job_row(a.id)])
        await repo.delete_target(a.id)
        return (await repo.get_route(route.id)).target_ids, (await repo.get_target(tg.id)).fallback_target_id, await repo.counts_by_status()

    target_ids, fallback, counts = run(scenario())
    assert target_ids == [2] and fallback is None and counts == {}


def test_fetch_due_jobs_round_robin_across_tasks_and_live_watch(tmp_path):
    async def scenario():
        repo = await make_repo(tmp_path)
        try:
            target = await repo.add_target(name="t", type="local", config={})
            route = await repo.add_route(name="r", source_chat_id="-1001", target_ids=[target.id])
            task_big = await repo.create_task(kind="adhoc", source_chat_id="-1001", target_ids=[target.id], status="queued")
            task_small = await repo.create_task(kind="adhoc", source_chat_id="-1002", target_ids=[target.id], status="queued")
            # Enqueue 3 jobs for big task first, then 2 for small task, then 1 live-watch job (task_id=None)
            await repo.enqueue_jobs(
                [
                    job_row(target.id, "big1", task_id=task_big.id, source_message_id=1),
                    job_row(target.id, "big2", task_id=task_big.id, source_message_id=2),
                    job_row(target.id, "big3", task_id=task_big.id, source_message_id=3),
                    job_row(target.id, "small1", task_id=task_small.id, source_message_id=10),
                    job_row(target.id, "small2", task_id=task_small.id, source_message_id=11),
                    job_row(target.id, "watch1", task_id=None, route_id=route.id, source_message_id=99),
                ]
            )
            due = await repo.fetch_due_jobs(10)
            return [j.dedupe_key for j in due]
        finally:
            await repo.engine.dispose()

    keys = run(scenario())
    # Round 1: live watch first, then 1st of big task, then 1st of small task
    # Round 2: 2nd of big task, 2nd of small task
    # Round 3: 3rd of big task
    assert keys == ["watch1", "big1", "small1", "big2", "small2", "big3"]
