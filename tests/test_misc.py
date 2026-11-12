from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telethon.tl import types

from app.bot.context import BotState, PendingSelection
from app.bot.handlers_config import config_view, rules_view
from app.bot.handlers_route import _parse_range, _parse_source, route_view, routes_view
from app.bot.handlers_target import target_view, targets_view
from app.bot.handlers_task import (
    _selection_view,
    handle_selection_text,
    help_text,
    start_view,
    task_detail_view,
    tasks_view,
)
from app.bot.ui import extract_secret_text, paginate_items, sync_bot_commands, target_id_list, yes_no
from app.db.engine import create_engine, init_models, make_sessionmaker
from app.db.repository import Repository
from app.engine.filters import ContentFilter, Rule, validate_rule
from app.logging import redact
from app.security import SecretBox, load_or_create_key
from app.settings_store import SettingsStore


def test_content_filter():
    rules = [Rule(1, "keyword", "Sponsored"), Rule(2, "regex", r"https?://\S+")]
    cf = ContentFilter(rules, enabled=True, case_sensitive=False)
    assert cf.match_text("this is SPONSORED").rule_id == 1
    assert cf.match_texts([None, "see https://x.y"]).rule_type == "regex"
    assert cf.match_text("clean") is None
    assert ContentFilter(rules, enabled=False, case_sensitive=False).match_text("sponsored") is None
    with pytest.raises(ValueError):
        validate_rule("regex", "(")


def test_secret_box(tmp_path):
    box = SecretBox(load_or_create_key("", tmp_path))
    token = box.encrypt("hello")
    assert token != "hello" and box.decrypt(token) == "hello"
    assert box.encrypt(token) == token  # idempotent
    assert (tmp_path / "secret.key").stat().st_mode & 0o777 == 0o600
    other = SecretBox(load_or_create_key("a passphrase", tmp_path))
    with pytest.raises(ValueError):
        other.decrypt(token)


def test_log_redaction():
    text = redact("token 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw url https://user:pw@dav.example/x")
    assert "AAHdq" not in text and "pw@" not in text and "user:***@" in text


def test_bot_parsers():
    assert target_id_list("1, #2，3") == [1, 2, 3]
    with pytest.raises(ValueError):
        target_id_list("a")
    assert yes_no("是") is True and yes_no("n") is False
    assert _parse_range("100 - 5") == (5, 100)
    assert _parse_range("https://t.me/c/1/10-20") == (10, 20)
    assert _parse_source("https://t.me/c/1/9/100") == {"chat": "-1001", "topic_id": 9}
    assert _parse_source("-1002 7") == {"chat": "-1002", "topic_id": 7}


def test_extract_secret_text_protects_markdown_symbols():
    # Plain password without entities
    evt_plain = SimpleNamespace(raw_text="p@ss!123", message=SimpleNamespace(entities=None))
    assert extract_secret_text(evt_plain) == "p@ss!123"

    # Password wrapped in `code` entity (Telegram preserves _, *, ~ inside code)
    evt_code = SimpleNamespace(
        raw_text="p@ss_w0rd*123~",
        message=SimpleNamespace(entities=[types.MessageEntityCode(offset=0, length=14)]),
    )
    assert extract_secret_text(evt_code) == "p@ss_w0rd*123~"

    # Password wrapped in literal backticks when client didn't create entity
    evt_literal_ticks = SimpleNamespace(raw_text="`p@ss_w0rd_123`", message=SimpleNamespace(entities=[]))
    assert extract_secret_text(evt_literal_ticks) == "p@ss_w0rd_123"

    # Password where Telegram ate _ or * into Italic/Bold entity -> must reject and warn
    evt_italic = SimpleNamespace(
        raw_text="p@ssw0rd123",
        message=SimpleNamespace(entities=[types.MessageEntityItalic(offset=4, length=4)]),
    )
    with pytest.raises(ValueError, match="backticks"):
        extract_secret_text(evt_italic, lang="en")
    with pytest.raises(ValueError, match="反引号"):
        extract_secret_text(evt_italic, lang="zh")


def test_subpanels_have_back_to_main_menu_and_bilingual_labels(tmp_path):
    async def scenario():
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        try:
            await init_models(engine)
            repo = Repository(engine, make_sessionmaker(engine), SecretBox(load_or_create_key("", tmp_path)))
            store = SettingsStore(repo)
            await store.load()
            ctx = SimpleNamespace(repo=repo, store=store, lang="en")
            def has_home(rows, expected_label):
                return any(
                    expected_label in getattr(btn, "text", str(btn))
                    for row in rows
                    for btn in row
                )

            for lang in ("en", "zh"):
                await store.set("ui.language", lang)
                ctx.lang = lang
                _, tg_rows = await targets_view(ctx)
                _, rt_rows = await routes_view(ctx)
                cfg_text, cfg_rows = config_view(ctx, "main")
                expected = "Main Menu" if lang == "en" else "主菜单"
                assert has_home(tg_rows, expected)
                assert has_home(rt_rows, expected)
                assert has_home(cfg_rows, expected)
                if lang == "en":
                    assert "Settings" in cfg_text
                else:
                    assert "设置" in cfg_text

                # Verify /start vs /help split
                st_text, st_rows = await start_view(ctx)
                hp_text = help_text(lang)
                assert st_text != hp_text
                if lang == "en":
                    assert "Quick Setup & Usage" in st_text
                    assert "Command Reference" in hp_text
                else:
                    assert "初次配置与使用" in st_text
                    assert "命令速查字典" in hp_text
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_paginated_list_views_and_zero_item_buttons(tmp_path):
    async def scenario():
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'page.db'}")
        try:
            await init_models(engine)
            repo = Repository(engine, make_sessionmaker(engine), SecretBox(load_or_create_key("", tmp_path)))
            store = SettingsStore(repo, default_language="zh")
            await store.load()
            ctx = SimpleNamespace(repo=repo, store=store, lang="zh", state=BotState())

            # Create 15 targets, 12 routes, and 12 tasks
            for i in range(1, 16):
                await repo.add_target(name=f"tg_{i}", type="local", config={"path": f"p{i}"})
            for i in range(1, 13):
                await repo.add_route(
                    name=f"rt_{i}",
                    source_chat_id=f"-100{i}",
                    source_title=f"src_{i}",
                    target_ids=[1],
                    watch_enabled=True,
                )
            for i in range(1, 13):
                await repo.create_task(
                    kind="adhoc",
                    source_chat_id="-100999",
                    start_id=i,
                    end_id=i,
                    target_ids=[1],
                    created_by=1,
                    status="queued",
                )

            def cb_data(rows):
                return [
                    getattr(btn, "data", None)
                    or getattr(getattr(btn, "type", None), "data", None)
                    or getattr(getattr(btn, "button", None), "data", b"")
                    for row in rows
                    for btn in row
                ]

            # 1) /targets page 0 shows 10 items in text, pagination row, and NO per-target buttons (tg:v:*)
            tg_text_0, tg_rows_0 = await targets_view(ctx, page=0)
            assert "(1/2)" in tg_text_0
            assert "tg_1" in tg_text_0 and "tg_10" in tg_text_0 and "tg_11" not in tg_text_0
            assert "/targets &lt;ID&gt;" in tg_text_0
            tg_callbacks = cb_data(tg_rows_0)
            assert not any(cb.startswith(b"tg:v:") for cb in tg_callbacks)
            assert b"tg:list:1" in tg_callbacks

            tg_text_1, _ = await targets_view(ctx, page=1)
            assert "(2/2)" in tg_text_1 and "tg_15" in tg_text_1

            # Direct target view still provides single-target action buttons
            detail_tg_text, detail_tg_rows = await target_view(ctx, 1)
            assert "tg_1" in detail_tg_text
            assert any(cb.startswith(b"tg:en:1") for cb in cb_data(detail_tg_rows))

            # 2) /routes page 0 shows 10 items in text, pagination row, and NO per-route buttons (rt:v:*)
            rt_text_0, rt_rows_0 = await routes_view(ctx, page=0)
            assert "(1/2)" in rt_text_0
            assert "rt_1" in rt_text_0 and "rt_10" in rt_text_0 and "rt_11" not in rt_text_0
            rt_callbacks = cb_data(rt_rows_0)
            assert not any(cb.startswith(b"rt:v:") for cb in rt_callbacks)
            assert b"rt:list:1" in rt_callbacks

            detail_rt_text, _ = await route_view(ctx, 1)
            assert "rt_1" in detail_rt_text

            # 3) /tasks page 0 shows 10 items in text, pagination row, and NO per-task buttons
            tk_text_0, tk_rows_0 = await tasks_view(ctx, page=0)
            assert "(1/2)" in tk_text_0
            tk_callbacks = cb_data(tk_rows_0)
            assert not any(cb.startswith((b"tk:x:", b"tk:r:")) for cb in tk_callbacks)
            assert b"m:tasks:1" in tk_callbacks

            # Single task detail view has cancel button
            tk_detail_text, tk_detail_rows = await task_detail_view(ctx, 1)
            assert "#1" in tk_detail_text
            assert any(cb == b"tk:x:1" for cb in cb_data(tk_detail_rows))

            # 4) Link selection card lists 10 targets per page as text + reply prompt, with no target toggle buttons
            targets = await repo.list_targets(enabled_only=True)
            sel = PendingSelection(user_id=1, chat="-100888", topic_id=None, start=1, end=5)
            sel_text, sel_rows = _selection_view(ctx, "tok1", sel, targets, page=0)
            assert "(1/2)" in sel_text and "回复目的地编号" in sel_text
            sel_callbacks = cb_data(sel_rows)
            assert b"sel:tok1:p:1" in sel_callbacks
            assert b"sel:tok1:x" in sel_callbacks
            assert not any(cb == b"sel:tok1:1" for cb in sel_callbacks)

            # 5) Bot command registration helper (clears default scope and sets Peer scope ONLY for admins)
            calls = []

            async def fake_bot(req):
                calls.append(req)

            await sync_bot_commands(fake_bot, "zh", admin_ids=[12345])
            assert len(calls) == 2
            assert isinstance(calls[0].scope, types.BotCommandScopeDefault)
            assert isinstance(calls[1].scope, types.BotCommandScopePeer)
            cmd_names = [c.command for c in calls[1].commands]
            assert {"start", "help", "targets", "routes", "tasks", "errors"}.issubset(cmd_names)

            # 6) Filter rules view includes /filter_add hints and Add Keyword / Add Regex wizard buttons
            from app.bot.handlers_config import _filter_wizard, config_view, rules_view

            empty_rules_text, empty_rules_rows = await rules_view(ctx)
            assert "/filter_add keyword" in empty_rules_text and "/filter_add regex" in empty_rules_text
            assert {b"cfg:fadd:keyword", b"cfg:fadd:regex"}.issubset(cb_data(empty_rules_rows))

            await repo.add_filter("keyword", "广告")
            non_empty_text, non_empty_rows = await rules_view(ctx)
            assert "/filter_add keyword" in non_empty_text and "/filter_add regex" in non_empty_text
            assert "/filter_del" in non_empty_text and "/filter_toggle" in non_empty_text
            assert {b"cfg:fadd:keyword", b"cfg:fadd:regex"}.issubset(cb_data(non_empty_rows))

            _, cfg_filter_rows = config_view(ctx, section="filters")
            assert {b"cfg:fadd:keyword", b"cfg:fadd:regex"}.issubset(cb_data(cfg_filter_rows))

            regex_wiz = _filter_wizard(ctx, "regex")
            assert "(广告|推广|抽奖|合作)" in regex_wiz.steps[0].prompt
            assert "https?://|t\\.me/(\\+|joinchat/)" in regex_wiz.steps[0].prompt
            assert regex_wiz.steps[0].parse("/filter_add regex (a|b)") == "(a|b)"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_errors_view_blocked_skip_retry_and_csv_report(tmp_path):
    from app.bot.handlers_task import errors_view
    from app.engine.notifier import Notifier
    from app.engine.report import problem_report_csv
    from app.engine.worker import Worker

    async def scenario():
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'err.db'}")
        try:
            await init_models(engine)
            repo = Repository(engine, make_sessionmaker(engine), SecretBox(load_or_create_key("", tmp_path)))
            store = SettingsStore(repo, default_language="zh")
            await store.load()
            ctx = SimpleNamespace(repo=repo, store=store, lang="zh", state=BotState())

            target = await repo.add_target(name="NAS", type="local", config={"path": "nas"})
            route = await repo.add_route(name="LiveRoute", source_chat_id="-100777", target_ids=[target.id], watch_enabled=True)
            task = await repo.create_task(
                kind="adhoc",
                source_chat_id="-100123456",
                source_title="测试源群",
                start_id=10,
                end_id=14,
                target_ids=[target.id],
                notify_chat_id=999,
                status="queued",
            )

            await repo.enqueue_jobs(
                [
                    # 1) Done job (with oversized_fallback)
                    {
                        "task_id": task.id,
                        "target_id": target.id,
                        "source_chat_id": "-100123456",
                        "source_title": "测试源群",
                        "source_message_id": 10,
                        "source_message_ids": [10],
                        "dedupe_key": "m:10",
                        "media_type": "video",
                        "file_size": 3 * 1024**3,
                        "status": "done",
                        "reason_code": "oversized_fallback",
                    },
                    # 2) Normal filtered skip (must NOT appear in /errors and must NOT be retried)
                    {
                        "task_id": task.id,
                        "target_id": target.id,
                        "source_chat_id": "-100123456",
                        "source_title": "测试源群",
                        "source_message_id": 11,
                        "source_message_ids": [11],
                        "dedupe_key": "m:11",
                        "media_type": "text",
                        "status": "skipped",
                        "reason_code": "content_filtered",
                        "last_error": "Skipped by keyword filter rule #1",
                    },
                    # 3) Actionable blocked skip (permission_denied)
                    {
                        "task_id": task.id,
                        "target_id": target.id,
                        "source_chat_id": "-100123456",
                        "source_title": "测试源群",
                        "source_message_id": 12,
                        "source_message_ids": [12],
                        "dedupe_key": "m:12",
                        "media_type": "video",
                        "file_size": 50 * 1024**2,
                        "status": "skipped",
                        "reason_code": "permission_denied",
                        "last_error": "ChatWriteForbiddenError: cannot write",
                    },
                    # 4) Failed job in task
                    {
                        "task_id": task.id,
                        "target_id": target.id,
                        "source_chat_id": "-100123456",
                        "source_title": "测试源群",
                        "source_message_id": 13,
                        "source_message_ids": [13],
                        "dedupe_key": "m:13",
                        "media_type": "document",
                        "status": "failed",
                        "reason_code": "network_error",
                        "attempts": 3,
                        "last_error": "Connection reset by peer",
                    },
                    # 5) Live Watch Route failed job (task_id=None)
                    {
                        "task_id": None,
                        "route_id": route.id,
                        "target_id": target.id,
                        "source_chat_id": "-100777",
                        "source_title": "监听群",
                        "source_message_id": 88,
                        "source_message_ids": [88],
                        "dedupe_key": "m:88",
                        "media_type": "photo",
                        "status": "failed",
                        "reason_code": "network_error",
                        "attempts": 3,
                        "last_error": "Timeout reading media",
                    },
                ]
            )

            def cb_data(rows):
                return [
                    getattr(btn, "data", None)
                    or getattr(getattr(btn, "type", None), "data", None)
                    or getattr(getattr(btn, "button", None), "data", b"")
                    for row in rows
                    for btn in row
                ]

            # Summary separates filtered (1) vs blocked (1) vs failed (1)
            summary = await repo.task_summary_counts(task.id)
            assert summary == {"done": 1, "filtered": 1, "blocked": 1, "skipped": 2, "failed": 1, "open": 0}

            # Task completion notification attaches View Issues + Retry buttons
            sent_notifications = []

            class FakeBot:
                async def send_message(self, chat_id, text, **kwargs):
                    sent_notifications.append((chat_id, text, kwargs))

            notifier = Notifier((999,))
            notifier.bot = FakeBot()
            dummy_worker = Worker.__new__(Worker)
            dummy_worker.repo = repo
            dummy_worker.store = store
            dummy_worker.notifier = notifier
            await dummy_worker._maybe_finish_task(task.id)
            assert len(sent_notifications) == 1
            _, notif_text, notif_kwargs = sent_notifications[0]
            assert "规则过滤 1 · 异常阻断 1 · 失败 1" in notif_text
            notif_cbs = cb_data(notif_kwargs["buttons"])
            assert f"err:t:{task.id}:0".encode() in notif_cbs
            assert f"tk:r:{task.id}".encode() in notif_cbs
            assert f"tk:rb:{task.id}".encode() in notif_cbs

            # Task-scoped errors_view shows #12 (blocked) and #13 (failed) with clickable link, excluding #11 (filtered)
            t_err_text, t_err_rows = await errors_view(ctx, task_id=task.id)
            assert "https://t.me/c/123456/12" in t_err_text
            assert "https://t.me/c/123456/13" in t_err_text
            assert "#11" not in t_err_text
            assert "目标群权限不足" in t_err_text and "ChatWriteForbiddenError" in t_err_text
            t_err_cbs = cb_data(t_err_rows)
            assert f"err:rf:t:{task.id}:0".encode() in t_err_cbs
            assert f"err:rb:t:{task.id}:0".encode() in t_err_cbs

            # Global errors_view also includes live Watch Route job #88 ([R#1])
            g_err_text, _ = await errors_view(ctx, task_id=None)
            assert "[R#1]" in g_err_text and "https://t.me/c/777/88" in g_err_text

            # CSV report includes failed + blocked + oversized_fallback (3 rows for task), excluding content_filtered
            csv_rows, csv_bytes = await problem_report_csv(repo, task_id=task.id)
            assert csv_rows == 3
            csv_str = csv_bytes.decode("utf-8-sig")
            assert "Connection reset by peer" in csv_str
            assert "ChatWriteForbiddenError" in csv_str
            assert "content_filtered" not in csv_str

            # Default retry_jobs only re-queues failed (1 item in task), leaving blocked skip untouched
            requeued_failed = await repo.retry_jobs(task_id=task.id, include_blocked=False)
            assert requeued_failed == 1
            after_first = await repo.task_summary_counts(task.id)
            assert after_first["open"] == 1 and after_first["blocked"] == 1 and after_first["filtered"] == 1

            # Retry with include_blocked=True re-queues blocked skip (#12), still leaving content_filtered (#11) untouched!
            requeued_blocked = await repo.retry_jobs(task_id=task.id, include_blocked=True)
            assert requeued_blocked == 1
            after_second = await repo.task_summary_counts(task.id)
            assert after_second["open"] == 2 and after_second["blocked"] == 0 and after_second["filtered"] == 1
            assert (await repo.get_task(task.id)).status == "queued"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_flood_premium_wait_handling_and_client_threshold(tmp_path, monkeypatch):
    from telethon import errors

    from app.engine.notifier import Notifier
    from app.engine.worker import Worker
    from app.errors import FloodWaitDeferred
    from app.telegram.client_manager import ClientManager
    from app.telegram.errors_map import FLOOD_WAIT_ERRORS
    from app.telegram.limiter import TelegramLimiter
    from fakes import make_boot

    assert errors.FloodPremiumWaitError in FLOOD_WAIT_ERRORS
    assert errors.FloodWaitError in FLOOD_WAIT_ERRORS

    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr("app.telegram.limiter.asyncio.sleep", fake_sleep)

    async def scenario():
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'flood.db'}")
        try:
            await init_models(engine)
            repo = Repository(engine, make_sessionmaker(engine), SecretBox(load_or_create_key("", tmp_path)))
            store = SettingsStore(repo, default_language="zh")
            await store.load()
            for key in (
                "limits.global_min_delay_seconds",
                "limits.download_delay_seconds",
                "limits.floodwait_extra_min_seconds",
                "limits.floodwait_extra_max_seconds",
            ):
                await store.set(key, 0)

            limiter = TelegramLimiter(store)

            # 1) Layer 2: TelegramLimiter.call catches FloodPremiumWaitError <= threshold and retries cleanly
            calls = 0

            async def flaky_download():
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise errors.FloodPremiumWaitError(request=None, capture=4)
                return "ok"

            assert await limiter.call("download", flaky_download) == "ok"
            assert calls == 2
            assert 4 in slept

            # When FloodPremiumWaitError exceeds floodwait_defer_after_seconds (default 60), defer job
            async def long_premium_wait():
                raise errors.FloodPremiumWaitError(request=None, capture=75)

            with pytest.raises(FloodWaitDeferred) as exc_info:
                await limiter.call("download", long_premium_wait)
            assert exc_info.value.retry_after_seconds == 75

            # 2) Layer 1: ClientManager.make_client sets flood_sleep_threshold, request_retries, raise_last_call_error
            boot = make_boot(tmp_path)
            cm = ClientManager(boot, limiter)
            client = cm.make_client("user")
            try:
                assert client.flood_sleep_threshold == 60
                assert client._request_retries == 10
                assert client._raise_last_call_error is True

                cm.reader = client
                await store.set("limits.floodwait_defer_after_seconds", 90)
                assert client.flood_sleep_threshold == 90
            finally:
                await client.disconnect()

            # 3) Layer 3: Worker._handle_error defers raw FloodPremiumWaitError (e.g. from streaming iter_download)
            target = await repo.add_target(name="DAV", type="webdav", config={"url": "https://dav.example/a"})
            await repo.enqueue_jobs(
                [
                    {
                        "target_id": target.id,
                        "source_chat_id": "-100123",
                        "source_message_id": 42,
                        "source_message_ids": [42],
                        "dedupe_key": "m:42",
                        "media_type": "video",
                        "status": "pending",
                    }
                ]
            )
            due = await repo.fetch_due_jobs(1)
            job = due[0]
            attempts = await repo.claim_job(job.id)
            assert attempts == 1

            worker = Worker.__new__(Worker)
            worker.repo = repo
            worker.store = store
            worker.notifier = Notifier((1,))
            await worker._handle_error(job, attempts, errors.FloodPremiumWaitError(request=None, capture=12))

            refreshed = await repo.get_job(job.id)
            assert refreshed.status == "pending"
            assert refreshed.attempts == 0  # defer_job rolls back attempt so retries are never burned
            assert refreshed.reason_code == "flood_wait"
            assert refreshed.next_retry_at is not None
            assert "FloodPremiumWaitError" in (refreshed.last_error or "")
        finally:
            await engine.dispose()

    asyncio.run(scenario())
