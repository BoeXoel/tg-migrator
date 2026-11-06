"""Status panel, link-driven tasks, queue control, and language switcher."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import Button, TelegramClient, events

from app.bot.auth import admin_only
from app.bot.context import AppContext, PendingSelection
from app.bot.ui import back_button, chunk_buttons, command, command_args, esc, status_labels, target_label, tr
from app.config import normalize_lang
from app.db.models import TERMINAL_STATUSES
from app.engine.report import oversized_csv
from app.engine.storage_guard import format_bytes, free_bytes
from app.telegram.link_parser import ParsedLink

log = logging.getLogger("migrator.bot.task")

HELP_TEXT_EN = """<b>🤖 TG Migrator Control Panel</b>

<b>Quick Transfer</b>
Send any Telegram message link directly:
• Single: <code>https://t.me/c/123456/789</code>
• Range: <code>https://t.me/c/123456/100-500</code> or two links
• Forum Topic links (<code>/c/chat/topic/msg</code>) are detected automatically

<b>Targets (Destinations)</b>
/targets Manage · /bind Bind Telegram group/channel/topic
/add_local Local directory · /add_webdav WebDAV server
/add_tg <code>chat [topic]</code> Add Telegram target manually

<b>Watch Routes</b>
/routes Manage · /add_route Create route
/route_set <code>id key value</code> Per-route setting override

<b>Settings & Filters</b>
/config Panel · /get <code>[section]</code> · /set <code>key value</code>
/lang Switch language (English / 简体中文)
/filters Rules · /filter_add <code>keyword|regex pattern</code> · /filter_del <code>id</code>
/export <code>[secrets]</code> Export YAML · /import <code>[replace]</code> Import YAML

<b>Queue & Maintenance</b>
/status Status · /tasks Tasks · /retry <code>[task_id]</code>
/pause · /resume · /recover · /cancel_task <code>id</code>
/verify Verify delivered messages · /report Oversized CSV report

<b>Account</b>
/login Log in reader account · /logout Log out · /cancel Abort wizard"""

HELP_TEXT_ZH = """<b>🤖 TG Migrator 控制台</b>

<b>快速搬运</b>
直接发送消息链接即可：
• 单条：<code>https://t.me/c/123456/789</code>
• 范围：<code>https://t.me/c/123456/100-500</code> 或两条链接
• Topic 链接（<code>/c/群/Topic/消息</code>）会自动识别

<b>目的地</b>
/targets 管理 · /bind 绑定 Telegram 群/频道/Topic
/add_local 本地目录 · /add_webdav WebDAV
/add_tg <code>chat [topic]</code> 手动添加 Telegram 目的地

<b>长期搬运 / 监听</b>
/routes 管理 · /add_route 新建路由
/route_set <code>id 设置项 值</code> 路由级覆盖

<b>设置</b>
/config 面板 · /get <code>[分区]</code> · /set <code>设置项 值</code>
/lang 切换语言（English / 简体中文）
/filters 过滤规则 · /filter_add <code>keyword|regex 内容</code> · /filter_del <code>id</code>
/export <code>[secrets]</code> 导出 YAML · /import <code>[replace]</code> 导入

<b>队列</b>
/status 状态 · /tasks 任务 · /retry <code>[任务id]</code>
/pause · /resume · /recover · /cancel_task <code>id</code>
/verify 校验已发送消息 · /report 导出超限文件 CSV

<b>账号</b>
/login 登录读取账号 · /logout 退出 · /cancel 取消当前操作"""

HELP_TEXT = HELP_TEXT_EN


def help_text(lang: str = "en") -> str:
    return HELP_TEXT_ZH if lang == "zh" else HELP_TEXT_EN


def main_menu(lang: str = "en") -> list[list[Any]]:
    return [
        [
            Button.inline(tr(lang, "📊 Status", "📊 状态"), b"m:status"),
            Button.inline(tr(lang, "📋 Tasks", "📋 任务"), b"m:tasks"),
        ],
        [
            Button.inline(tr(lang, "🎯 Targets", "🎯 目的地"), b"tg:list"),
            Button.inline(tr(lang, "🔁 Routes", "🔁 路由"), b"rt:list"),
        ],
        [
            Button.inline(tr(lang, "⚙️ Settings", "⚙️ 设置"), b"cfg:main"),
            Button.inline(tr(lang, "🌐 简体中文", "🌐 English"), b"m:lang"),
        ],
    ]


async def status_view(ctx: AppContext) -> tuple[str, list[list[Any]]]:
    lang = ctx.lang
    clients = ctx.clients
    counts = await ctx.repo.counts_by_status()
    settings = ctx.store.current
    if clients.reader_me is not None:
        reader_caps = clients.reader_capabilities()
        reader = f"{esc(getattr(clients.reader_me, 'first_name', ''))} ({clients.reader_me.id}) · {reader_caps.account_type}"
    else:
        reader = tr(lang, "❌ Not logged in (send /login)", "❌ 未登录（发送 /login）")
    try:
        writer_caps = clients.writer_capabilities(settings.transfer.max_upload_bytes)
        limit_str = format_bytes(writer_caps.max_upload_bytes)
        writer = f"{writer_caps.identity} · {tr(lang, f'limit {limit_str}', f'上限 {limit_str}')}"
    except Exception:
        writer = tr(lang, "Unavailable", "不可用")
    labels = status_labels(lang)
    queue = " · ".join(f"{label} {counts.get(key, 0)}" for key, label in labels.items())
    worker = ctx.worker
    if worker.paused:
        worker_state = tr(lang, f"⏸ Paused: {esc(worker.pause_reason or '')}", f"⏸ 已暂停：{esc(worker.pause_reason or '')}")
    else:
        worker_state = tr(lang, "▶️ Running", "▶️ 运行中")
    dl_free = format_bytes(free_bytes(ctx.boot.downloads_dir))
    dl_min = format_bytes(settings.downloads.min_free_bytes)
    arch_free = format_bytes(free_bytes(ctx.boot.archive_root))
    lines = [
        tr(lang, "<b>📊 System Status</b>", "<b>📊 运行状态</b>"),
        f"{tr(lang, 'Reader', '读取账号')}: {reader}",
        f"{tr(lang, 'Writer', '写入账号')}: {esc(writer)}",
        f"{tr(lang, 'Queue', '队列')}: {queue}",
        f"Worker: {worker_state}",
        f"{tr(lang, 'Watched routes', '监听路由')}: {ctx.watcher.watched_count}",
        tr(
            lang,
            f"Disk (downloads): {dl_free} free · {dl_min} reserved",
            f"磁盘（下载）：剩余 {dl_free} · 保留 {dl_min}",
        ),
        tr(lang, f"Disk (archive): {arch_free} free", f"磁盘（归档）：剩余 {arch_free}"),
        f"{tr(lang, 'Database', '数据库')}: {esc(ctx.database_label)}",
        f"{tr(lang, 'Proxy', '代理')}: {esc(ctx.boot.proxy.describe())}",
    ]
    lines.extend(esc(line) for line in worker.status_lines(lang))
    toggle = (
        Button.inline(tr(lang, "▶️ Resume", "▶️ 恢复"), b"m:resume")
        if worker.paused
        else Button.inline(tr(lang, "⏸ Pause", "⏸ 暂停"), b"m:pause")
    )
    buttons = [
        [Button.inline(tr(lang, "🔄 Refresh", "🔄 刷新"), b"m:status"), toggle],
        [
            Button.inline(tr(lang, "♻️ Recover Interrupted", "♻️ 恢复中断任务"), b"m:recover"),
            Button.inline(tr(lang, "📋 Tasks", "📋 任务"), b"m:tasks"),
        ],
        [back_button(b"m:home", lang=lang)],
    ]
    return "\n".join(lines), buttons


async def tasks_view(ctx: AppContext) -> tuple[str, list[list[Any]]]:
    lang = ctx.lang
    tasks = await ctx.repo.list_tasks(10)
    if not tasks:
        return (
            tr(lang, "No tasks yet. Send a Telegram message link to create one.", "暂无任务。发送一条消息链接即可创建。"),
            [[Button.inline(tr(lang, "📊 Status", "📊 状态"), b"m:status"), back_button(b"m:home", lang=lang)]],
        )
    lines = [tr(lang, "<b>📋 Recent Tasks</b>", "<b>📋 最近任务</b>")]
    buttons = []
    for task in tasks:
        counts = await ctx.repo.task_job_counts(task.id)
        open_jobs = sum(v for k, v in counts.items() if k not in TERMINAL_STATUSES)
        span = f"{task.start_id}-{task.end_id}" if task.end_id and task.end_id != task.start_id else f"{task.start_id}"
        lines.append(
            f"#{task.id} [{task.status}] {esc(task.source_title or task.source_chat_id)} · {span}\n"
            f"    ✅{counts.get('done', 0)} ⏭{counts.get('skipped', 0)} ❌{counts.get('failed', 0)} ⏳{open_jobs}"
        )
        if task.status in {"scanning", "queued"}:
            buttons.append(Button.inline(tr(lang, f"✖️ Cancel #{task.id}", f"✖️ 取消 #{task.id}"), f"tk:x:{task.id}".encode()))
        if counts.get("failed"):
            buttons.append(Button.inline(tr(lang, f"🔁 Retry #{task.id}", f"🔁 重试 #{task.id}"), f"tk:r:{task.id}".encode()))
    rows = chunk_buttons(buttons, 2)
    rows.append([Button.inline(tr(lang, "🔄 Refresh", "🔄 刷新"), b"m:tasks"), Button.inline(tr(lang, "📊 Status", "📊 状态"), b"m:status")])
    rows.append([back_button(b"m:home", lang=lang)])
    return "\n".join(lines), rows


# ---------------------------------------------------------------- link flow


def _selection_view(ctx: AppContext, token: str, selection: PendingSelection, targets: list[Any]) -> tuple[str, list]:
    lang = ctx.lang
    count = selection.end - selection.start + 1
    span = (
        f"{selection.start}"
        if selection.start == selection.end
        else tr(lang, f"{selection.start}–{selection.end} ({count} IDs)", f"{selection.start}–{selection.end}（{count} 个 ID）")
    )
    topic = f"\nTopic: {selection.topic_id}" if selection.topic_id else ""
    text = tr(
        lang,
        f"<b>📥 New Transfer Task</b>\nSource: <code>{esc(selection.chat)}</code>{topic}\nMessages: {span}\n\nSelect target(s):",
        f"<b>📥 新搬运任务</b>\n来源：<code>{esc(selection.chat)}</code>{topic}\n消息：{span}\n\n选择目的地（可多选）：",
    )
    buttons = [
        Button.inline(f"{'✅' if t.id in selection.selected else '⬜'} {target_label(t, lang)}", f"sel:{token}:{t.id}".encode())
        for t in targets
    ]
    rows = chunk_buttons(buttons, 1)
    rows.append(
        [
            Button.inline(tr(lang, "🚀 Start", "🚀 开始"), f"sel:{token}:go".encode()),
            Button.inline(tr(lang, "✖️ Cancel", "✖️ 取消"), f"sel:{token}:x".encode()),
        ]
    )
    return text, rows


async def handle_link(event: Any, ctx: AppContext, parsed: ParsedLink) -> None:
    lang = ctx.lang
    if ctx.clients.reader is None:
        await event.respond(tr(lang, "❌ Reader account is not logged in. Send /login first.", "❌ 读取账号尚未登录，请先发送 /login。"))
        return
    targets = await ctx.repo.list_targets(enabled_only=True)
    if not targets:
        await event.respond(
            tr(
                lang,
                "No enabled targets yet. Create one first with /bind, /add_local, or /add_webdav.",
                "还没有可用的目的地。先用 /bind、/add_local 或 /add_webdav 创建一个。",
            )
        )
        return
    token = ctx.state.new_token()
    selection = PendingSelection(
        user_id=event.sender_id, chat=parsed.chat, topic_id=parsed.topic_id, start=parsed.start, end=parsed.end
    )
    if len(targets) == 1:
        selection.selected.add(targets[0].id)
    ctx.state.selections[token] = selection
    text, buttons = _selection_view(ctx, token, selection, targets)
    await event.respond(text, buttons=buttons)


async def on_selection(event: Any, ctx: AppContext) -> None:
    lang = ctx.lang
    _, token, action = event.data.decode().split(":", 2)
    selection = ctx.state.selections.get(token)
    if selection is None or selection.user_id != event.sender_id:
        await event.answer(tr(lang, "Selection expired. Please resend the link.", "该选择已过期，请重新发送链接"), alert=True)
        return
    if action == "x":
        ctx.state.selections.pop(token, None)
        await event.edit(tr(lang, "Cancelled.", "已取消。"))
        return
    if action == "go":
        if not selection.selected:
            await event.answer(tr(lang, "Please select at least one target", "请至少选择一个目的地"), alert=True)
            return
        ctx.state.selections.pop(token, None)
        task = await ctx.repo.create_task(
            kind="adhoc",
            source_chat_id=selection.chat,
            source_topic_id=selection.topic_id,
            start_id=selection.start,
            end_id=selection.end,
            target_ids=sorted(selection.selected),
            created_by=event.sender_id,
            notify_chat_id=event.chat_id,
            status="scanning",
        )
        await event.edit(tr(lang, f"🔎 Scanning task #{task.id}…", f"🔎 任务 #{task.id} 扫描中…"))
        message = await event.get_message()
        launch_scan(ctx, task.id, event.chat_id, message.id)
        return
    target_id = int(action)
    selection.selected.symmetric_difference_update({target_id})
    targets = await ctx.repo.list_targets(enabled_only=True)
    text, buttons = _selection_view(ctx, token, selection, targets)
    await event.edit(text, buttons=buttons)


def launch_scan(ctx: AppContext, task_id: int, chat_id: int | None, message_id: int | None) -> None:
    async def runner() -> None:
        bot: TelegramClient = ctx.clients.bot
        lang = ctx.lang
        text = ""
        try:
            stats = await ctx.scanner.run_task(task_id)
            text = tr(
                lang,
                f"📥 Task #{task_id} queued {stats.enqueued} item(s)\n"
                f"Scanned {stats.groups} group(s) · Duplicates {stats.duplicates} · Filtered {stats.skipped}",
                f"📥 任务 #{task_id} 已入队 {stats.enqueued} 项\n"
                f"扫描 {stats.groups} 组 · 重复 {stats.duplicates} · 按规则跳过 {stats.skipped}",
            )
            finished = await ctx.repo.finish_task_if_complete(task_id)
            if finished is not None:
                text += tr(lang, "\n(No new items to process; task completed)", "\n（没有需要处理的新内容，任务已结束）")
            else:
                text += tr(lang, "\nYou will be notified when complete. Check /tasks for progress.", "\n完成后会通知你。/tasks 查看进度。")
        except asyncio.CancelledError:
            text = tr(lang, f"✖️ Task #{task_id} cancelled", f"✖️ 任务 #{task_id} 已取消")
        except Exception as exc:
            log.exception("Scan of task %s failed", task_id)
            text = tr(lang, f"❌ Task #{task_id} scan failed: {esc(exc)}", f"❌ 任务 #{task_id} 扫描失败：{esc(exc)}")
        finally:
            ctx.state.scan_tasks.pop(task_id, None)
            if chat_id and bot is not None:
                try:
                    if message_id:
                        await bot.edit_message(chat_id, message_id, text)
                    else:
                        await bot.send_message(chat_id, text)
                except Exception:
                    await bot.send_message(chat_id, text)

    ctx.state.scan_tasks[task_id] = asyncio.create_task(runner(), name=f"scan-{task_id}")


# ----------------------------------------------------------------- commands


async def cmd_start(event: Any, ctx: AppContext) -> None:
    await event.respond(help_text(ctx.lang), buttons=main_menu(ctx.lang))


async def cmd_lang(event: Any, ctx: AppContext) -> None:
    arg = command_args(event)
    if arg:
        try:
            new_lang = normalize_lang(arg)
        except ValueError as exc:
            await event.respond(f"❌ {esc(exc)}")
            return
    else:
        new_lang = "zh" if ctx.lang == "en" else "en"
    await ctx.store.set("ui.language", new_lang)
    msg = "🌐 语言已切换为：<b>简体中文</b>" if new_lang == "zh" else "🌐 Language switched to: <b>English</b>"
    await event.respond(msg + "\n\n" + help_text(new_lang), buttons=main_menu(new_lang))


async def cmd_status(event: Any, ctx: AppContext) -> None:
    text, buttons = await status_view(ctx)
    await event.respond(text, buttons=buttons)


async def cmd_tasks(event: Any, ctx: AppContext) -> None:
    text, buttons = await tasks_view(ctx)
    await event.respond(text, buttons=buttons)


async def cmd_pause(event: Any, ctx: AppContext) -> None:
    await ctx.worker.pause("Paused by admin")
    await event.respond(
        ctx.t(
            "⏸ Queue paused (takes effect after the current job finishes). Send /resume to continue.",
            "⏸ 队列已暂停（当前任务完成后生效）。/resume 恢复。",
        )
    )


async def cmd_resume(event: Any, ctx: AppContext) -> None:
    await ctx.worker.resume()
    await event.respond(ctx.t("▶️ Queue resumed.", "▶️ 队列已恢复。"))


async def cmd_recover(event: Any, ctx: AppContext) -> None:
    if ctx.worker.active:
        await event.respond(
            ctx.t(
                "A job is currently active. Please /pause and wait for it to finish before recovering.",
                "有任务正在执行，请先 /pause 并等待其结束后再恢复中断任务。",
            )
        )
        return
    count = await ctx.repo.recover_in_progress()
    ctx.worker.wake()
    await event.respond(
        ctx.t(
            f"♻️ Reset {count} interrupted job(s) back to pending.",
            f"♻️ 已将 {count} 个中断任务恢复为待处理。",
        )
    )


async def cmd_retry(event: Any, ctx: AppContext) -> None:
    args = command_args(event)
    task_id = int(args.lstrip("#")) if args.strip().lstrip("#").isdigit() else None
    count = await ctx.repo.retry_jobs(task_id=task_id)
    ctx.worker.wake()
    await event.respond(ctx.t(f"🔁 Re-queued {count} failed job(s).", f"🔁 已重新排队 {count} 个失败任务。"))


async def cmd_cancel_task(event: Any, ctx: AppContext) -> None:
    args = command_args(event).lstrip("#")
    if not args.isdigit():
        await event.respond(ctx.t("Usage: /cancel_task <task_id>", "用法：/cancel_task 任务ID"))
        return
    await event.respond(await _cancel(ctx, int(args)))


async def _cancel(ctx: AppContext, task_id: int) -> str:
    scan = ctx.state.scan_tasks.get(task_id)
    if scan is not None:
        scan.cancel()
    count = await ctx.repo.cancel_task(task_id)
    return ctx.t(
        f"✖️ Task #{task_id} cancelled; {count} pending item(s) skipped.",
        f"✖️ 任务 #{task_id} 已取消，{count} 个待处理项被跳过。",
    )


async def cmd_verify(event: Any, ctx: AppContext) -> None:
    message = await event.respond(ctx.t("🔍 Verifying delivered Telegram messages…", "🔍 正在校验已发送到 Telegram 的消息…"))
    stats = await ctx.verifier.run()
    await message.edit(
        ctx.t(
            f"🔍 Verification complete: checked {stats.checked} · verified {stats.verified} · missing {stats.incomplete} · errors {stats.errors}",
            f"🔍 校验完成：检查 {stats.checked} · 确认 {stats.verified} · 缺失 {stats.incomplete} · 出错 {stats.errors}",
        )
    )


async def cmd_report(event: Any, ctx: AppContext) -> None:
    rows, content = await oversized_csv(ctx.repo)
    if not rows:
        await event.respond(ctx.t("No oversized skipped file records found.", "没有超限文件记录。"))
        return
    await event.respond(
        ctx.t(f"📄 Oversized skipped files: {rows}", f"📄 超限文件 {rows} 条"),
        file=_named_bytes(content, "oversized.csv"),
    )


def _named_bytes(content: bytes, name: str) -> Any:
    import io

    buffer = io.BytesIO(content)
    buffer.name = name
    return buffer


# ---------------------------------------------------------------- callbacks


async def on_menu(event: Any, ctx: AppContext) -> None:
    action = event.data.decode().split(":", 1)[1]
    if action in {"help", "home"}:
        await event.edit(help_text(ctx.lang), buttons=main_menu(ctx.lang))
        return
    if action == "lang":
        new_lang = "zh" if ctx.lang == "en" else "en"
        await ctx.store.set("ui.language", new_lang)
        await event.answer("已切换为简体中文" if new_lang == "zh" else "Switched to English")
        await event.edit(help_text(new_lang), buttons=main_menu(new_lang))
        return
    if action == "pause":
        await ctx.worker.pause("Paused by admin")
    elif action == "resume":
        await ctx.worker.resume()
    elif action == "recover":
        if ctx.worker.active:
            await event.answer(ctx.t("Active jobs running; pause first", "有任务正在执行，请先暂停"), alert=True)
            return
        count = await ctx.repo.recover_in_progress()
        ctx.worker.wake()
        await event.answer(ctx.t(f"Recovered {count} interrupted job(s)", f"已恢复 {count} 个中断任务"))
    if action == "tasks":
        text, buttons = await tasks_view(ctx)
    else:
        text, buttons = await status_view(ctx)
    await event.edit(text, buttons=buttons)


async def on_task_button(event: Any, ctx: AppContext) -> None:
    _, action, raw_id = event.data.decode().split(":", 2)
    task_id = int(raw_id)
    if action == "x":
        await event.answer(await _cancel(ctx, task_id))
    elif action == "r":
        count = await ctx.repo.retry_jobs(task_id=task_id)
        ctx.worker.wake()
        await event.answer(ctx.t(f"Re-queued {count} item(s)", f"已重新排队 {count} 项"))
    text, buttons = await tasks_view(ctx)
    await event.edit(text, buttons=buttons)


def register(bot: TelegramClient, ctx: AppContext) -> None:
    commands = {
        "start": cmd_start,
        "help": cmd_start,
        "lang": cmd_lang,
        "status": cmd_status,
        "tasks": cmd_tasks,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "recover": cmd_recover,
        "retry": cmd_retry,
        "cancel_task": cmd_cancel_task,
        "verify": cmd_verify,
        "report": cmd_report,
    }
    for name, handler in commands.items():
        bot.add_event_handler(admin_only(ctx, handler), events.NewMessage(pattern=command(name)))
    bot.add_event_handler(admin_only(ctx, on_menu), events.CallbackQuery(pattern=rb"^m:"))
    bot.add_event_handler(admin_only(ctx, on_selection), events.CallbackQuery(pattern=rb"^sel:"))
    bot.add_event_handler(admin_only(ctx, on_task_button), events.CallbackQuery(pattern=rb"^tk:"))
