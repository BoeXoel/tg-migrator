"""Start/Help panels, status panel, link-driven tasks, queue control, and language switcher."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import Button, TelegramClient, events

from app.bot.auth import admin_only
from app.bot.context import AppContext, PendingSelection
from app.bot.ui import (
    PAGE_SIZE,
    back_button,
    command,
    command_args,
    esc,
    paginate_items,
    pagination_row,
    reason_label,
    status_labels,
    sync_bot_commands,
    target_id_list,
    target_label,
    tr,
)
from app.config import normalize_lang
from app.db.models import TERMINAL_STATUSES
from app.engine.report import problem_report_csv
from app.engine.storage_guard import format_bytes, free_bytes
from app.telegram.link_parser import ParsedLink
from app.telegram.media_helper import source_link

log = logging.getLogger("migrator.bot.task")

HELP_TEXT_EN = """<b>📖 TG Migrator Command Reference</b>

<b>Quick Transfer</b>
Send any Telegram message link directly:
• Single: <code>https://t.me/c/123456/789</code>
• Range: <code>https://t.me/c/123456/100-500</code> or two links
• Forum Topic links (<code>/c/chat/topic/msg</code>) are detected automatically

<b>Targets (Destinations)</b>
/targets <code>[id]</code> List targets (paginated) or manage target <code>#id</code> (alias: /target)
/bind Bind Telegram group, channel, or Forum Sub-Topic
/add_local Local directory · /add_webdav WebDAV server
/add_tg <code>chat [topic]</code> Add Telegram target manually

<b>Watch Routes</b>
/routes <code>[id]</code> List routes (paginated) or manage route <code>#id</code> (alias: /route)
/add_route Create route
/route_set <code>id key value</code> · /route_unset <code>id key</code> Per-route setting override

<b>Settings & Filters</b>
/config Panel · /get <code>[section]</code> · /set <code>key value</code>
/lang Switch language (English / 简体中文)
/filters Rules · /filter_add <code>keyword|regex pattern</code> · /filter_del <code>id</code>
/export <code>[secrets]</code> Export YAML · /import <code>[replace]</code> Import YAML

<b>Queue & Maintenance</b>
/status Status · /tasks <code>[id]</code> List tasks or inspect task <code>#id</code> (alias: /task)
/errors <code>[task_id]</code> View failed & blocked items (alias: /failed)
/retry <code>[task_id] [all]</code> Retry failed items (add <code>all</code> to include blocked skips)
/pause · /resume · /recover · /cancel_task <code>id</code>
/verify Verify delivered messages · /report <code>[task_id]</code> Export issue & oversized CSV

<b>Account</b>
/login Log in reader account · /logout Log out · /cancel Abort operation"""

HELP_TEXT_ZH = """<b>📖 TG Migrator 命令速查字典</b>

<b>快速搬运</b>
直接发送消息链接即可创建任务：
• 单条：<code>https://t.me/c/123456/789</code>
• 范围：<code>https://t.me/c/123456/100-500</code> 或首尾两条链接
• 子话题链接（<code>/c/群/Topic/消息</code>）会自动识别

<b>目的地管理</b>
/targets <code>[id]</code> 分页查看目的地列表，或管理指定 <code>#id</code>（别名：/target）
/bind 在目标 Telegram 群/频道/子 Topic 内绑定目的地
/add_local 新建本地目录 · /add_webdav 新建 WebDAV
/add_tg <code>chat [topic]</code> 手动添加 Telegram 目的地

<b>持续监听路由</b>
/routes <code>[id]</code> 分页查看路由列表，或管理指定 <code>#id</code>（别名：/route）
/add_route 新建监听路由
/route_set <code>id 设置项 值</code> · /route_unset <code>id 设置项</code> 路由级参数覆盖

<b>参数设置与过滤</b>
/config 设置面板 · /get <code>[分区]</code> · /set <code>设置项 值</code>
/lang 切换语言（English / 简体中文）
/filters 过滤规则 · /filter_add <code>keyword|regex 内容</code> · /filter_del <code>id</code>
/export <code>[secrets]</code> 导出 YAML · /import <code>[replace]</code> 导入 YAML

<b>队列与维护</b>
/status 运行状态 · /tasks <code>[id]</code> 任务列表或管理指定 <code>#id</code>（别名：/task）
/errors <code>[任务id]</code> 查看失败与异常阻断明细（别名：/failed）
/retry <code>[任务id] [all]</code> 重试失败项（附加 <code>all</code> 连同异常阻断项一起重试）
/pause · /resume · /recover · /cancel_task <code>id</code>
/verify 校验已发送消息 · /report <code>[任务id]</code> 导出异常与超限 CSV 报表

<b>读取账号</b>
/login 登录读取账号 · /logout 退出登录 · /cancel 取消当前操作"""

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
            Button.inline(tr(lang, "📖 Help", "📖 帮助"), b"m:help"),
        ],
        [
            Button.inline(tr(lang, "🌐 简体中文", "🌐 English"), b"m:lang"),
        ],
    ]


async def start_view(ctx: AppContext) -> tuple[str, list[list[Any]]]:
    """Concise onboarding & readiness overview card for ``/start``."""

    lang = ctx.lang
    clients = getattr(ctx, "clients", None)
    if clients is not None and getattr(clients, "reader_me", None) is not None:
        me = clients.reader_me
        reader_line = f"✅ {esc(getattr(me, 'first_name', '') or me.id)} (<code>{me.id}</code>)"
    else:
        reader_line = tr(lang, "❌ Not logged in (send /login)", "❌ 未登录（请发送 /login）")

    targets = await ctx.repo.list_targets()
    enabled_targets = sum(1 for t in targets if t.enabled)
    routes = await ctx.repo.list_routes()
    watched_routes = sum(1 for r in routes if r.enabled and r.watch_enabled)
    counts = await ctx.repo.counts_by_status()
    pending_jobs = counts.get("pending", 0) + counts.get("downloading", 0) + counts.get("uploading", 0)

    worker = getattr(ctx, "worker", None)
    if worker is not None and getattr(worker, "paused", False):
        worker_badge = tr(lang, "⏸ Paused", "⏸ 已暂停")
    else:
        worker_badge = tr(lang, "▶️ Running", "▶️ 运行中")

    if lang == "zh":
        lines = [
            "<b>🤖 TG Migrator 控制台</b>",
            "",
            f"• <b>读取账号</b>：{reader_line}",
            f"• <b>目的地</b>：{enabled_targets} 个可用（共 {len(targets)} 个） · /targets",
            f"• <b>监听路由</b>：{watched_routes} 条监听中（共 {len(routes)} 条） · /routes",
            f"• <b>工作队列</b>：{worker_badge} · 待处理/进行中 {pending_jobs} 项 · /status",
            "",
            "<b>🚀 初次配置与使用</b>",
            "1. 发送 /login 登录个人读取账号",
            "2. 在目标群组或子话题内发送 /bind 绑定目的地（或 /add_local、/add_webdav）",
            "3. 直接在此发送消息链接并回复目的地编号即可启动搬运",
            "",
            "💡 <i>发送 /help 查看完整命令字典。</i>",
        ]
    else:
        lines = [
            "<b>🤖 TG Migrator Control Panel</b>",
            "",
            f"• <b>Reader Account</b>: {reader_line}",
            f"• <b>Targets</b>: {enabled_targets} enabled ({len(targets)} total) · /targets",
            f"• <b>Watch Routes</b>: {watched_routes} active ({len(routes)} total) · /routes",
            f"• <b>Queue</b>: {worker_badge} · {pending_jobs} open job(s) · /status",
            "",
            "<b>🚀 Quick Setup & Usage</b>",
            "1. Send /login to authenticate your personal Reader account",
            "2. Send /bind inside a target group/topic (or /add_local, /add_webdav)",
            "3. Send any Telegram message link here and reply with target ID(s) to transfer",
            "",
            "💡 <i>Send /help for the full command reference.</i>",
        ]
    return "\n".join(lines), main_menu(lang)


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
    prob = await ctx.repo.count_problem_jobs()
    if prob["total"]:
        lines.append(
            tr(
                lang,
                f"Issues: ❌Failed {prob['failed']} · ⚠️Blocked {prob['blocked']} (send /errors)",
                f"异常记录：❌失败 {prob['failed']} · ⚠️异常阻断 {prob['blocked']}（发送 /errors 查看）",
            )
        )
    lines.extend(esc(line) for line in worker.status_lines(lang))
    toggle = (
        Button.inline(tr(lang, "▶️ Resume", "▶️ 恢复"), b"m:resume")
        if worker.paused
        else Button.inline(tr(lang, "⏸ Pause", "⏸ 暂停"), b"m:pause")
    )
    err_label = (
        tr(lang, f"⚠️ Issues ({prob['total']})", f"⚠️ 异常明细 ({prob['total']})")
        if prob["total"]
        else tr(lang, "⚠️ Issues", "⚠️ 异常明细")
    )
    buttons = [
        [Button.inline(tr(lang, "🔄 Refresh", "🔄 刷新"), b"m:status"), toggle],
        [
            Button.inline(tr(lang, "📋 Tasks", "📋 任务"), b"m:tasks"),
            Button.inline(err_label, b"err:g:0"),
        ],
        [Button.inline(tr(lang, "♻️ Recover Interrupted", "♻️ 恢复中断任务"), b"m:recover")],
        [back_button(b"m:home", lang=lang)],
    ]
    return "\n".join(lines), buttons


async def tasks_view(ctx: AppContext, page: int = 0) -> tuple[str, list[list[Any]]]:
    lang = ctx.lang
    tasks = await ctx.repo.list_tasks(100)
    if not tasks:
        return (
            tr(lang, "No tasks yet. Send a Telegram message link to create one.", "暂无任务。发送一条消息链接即可创建。"),
            [[Button.inline(tr(lang, "📊 Status", "📊 状态"), b"m:status"), back_button(b"m:home", lang=lang)]],
        )
    page_items, page, total_pages = paginate_items(tasks, page, PAGE_SIZE)
    page_suffix = f" ({page + 1}/{total_pages})" if total_pages > 1 else ""
    lines = [tr(lang, f"<b>📋 Recent Tasks{page_suffix}</b>", f"<b>📋 最近任务{page_suffix}</b>")]
    for task in page_items:
        counts = await ctx.repo.task_summary_counts(task.id)
        span = f"{task.start_id}-{task.end_id}" if task.end_id and task.end_id != task.start_id else f"{task.start_id}"
        lines.append(
            f"• <code>#{task.id}</code> [{task.status}] {esc(task.source_title or task.source_chat_id)} · {span}\n"
            f"    ✅{counts['done']} ⏭{counts['filtered']} ⚠️{counts['blocked']} ❌{counts['failed']} ⏳{counts['open']}"
        )
    lines.append(
        tr(
            lang,
            "\n💡 <i>Send <code>/tasks &lt;ID&gt;</code> to inspect, view issues, cancel, or retry a task (e.g. <code>/tasks 1</code>).</i>",
            "\n💡 <i>发送 <code>/tasks &lt;ID&gt;</code> 查看详情、异常明细、取消或重试指定任务（如 <code>/tasks 1</code>）。</i>",
        )
    )
    rows: list[list[Any]] = []
    nav = pagination_row("m:tasks", page, total_pages, lang=lang)
    if nav:
        rows.append(nav)
    rows.append(
        [
            Button.inline(tr(lang, "🔄 Refresh", "🔄 刷新"), f"m:tasks:{page}".encode()),
            Button.inline(tr(lang, "📊 Status", "📊 状态"), b"m:status"),
        ]
    )
    rows.append([back_button(b"m:home", lang=lang)])
    return "\n".join(lines), rows


async def task_detail_view(ctx: AppContext, task_id: int) -> tuple[str, list[list[Any]]]:
    lang = ctx.lang
    task = await ctx.repo.get_task(task_id)
    if task is None:
        return tr(lang, "Task does not exist.", "任务不存在。"), [[back_button(b"m:tasks", lang=lang)]]
    counts = await ctx.repo.task_summary_counts(task.id)
    done = counts["done"]
    filtered = counts["filtered"]
    blocked = counts["blocked"]
    failed = counts["failed"]
    open_jobs = counts["open"]
    problems = failed + blocked
    span = f"{task.start_id}-{task.end_id}" if task.end_id and task.end_id != task.start_id else f"{task.start_id}"
    lines = [
        tr(lang, f"<b>📋 Task #{task.id}</b>", f"<b>📋 任务 #{task.id}</b>"),
        f"{tr(lang, 'Status', '状态')}: <code>{esc(task.status)}</code> ({esc(task.kind)})",
        f"{tr(lang, 'Source', '来源')}: {esc(task.source_title or task.source_chat_id)} <code>{esc(task.source_chat_id)}</code>",
    ]
    if task.source_topic_id:
        lines.append(f"Topic: <code>{task.source_topic_id}</code>")
    progress_line = tr(
        lang,
        f"Progress: ✅Done {done} · ⏭Filtered {filtered} · ⚠️Blocked {blocked} · ❌Failed {failed} · ⏳Open {open_jobs}",
        f"进度：✅成功 {done} · ⏭规则过滤 {filtered} · ⚠️异常阻断 {blocked} · ❌失败 {failed} · ⏳进行中 {open_jobs}",
    )
    lines.extend(
        [
            f"{tr(lang, 'Messages', '消息区间')}: <code>{span}</code>",
            f"{tr(lang, 'Targets', '目的地 ID')}: <code>{esc(task.target_ids)}</code>",
            progress_line,
        ]
    )
    if task.error:
        lines.append(f"{tr(lang, 'Error', '错误')}: <code>{esc(task.error)}</code>")

    rows: list[list[Any]] = []
    top_row: list[Any] = []
    if task.status in {"scanning", "queued"}:
        top_row.append(Button.inline(tr(lang, f"✖️ Cancel #{task.id}", f"✖️ 取消 #{task.id}"), f"tk:x:{task.id}".encode()))
    if problems:
        top_row.append(
            Button.inline(
                tr(lang, f"⚠️ View Issues ({problems})", f"⚠️ 异常明细 ({problems})"),
                f"err:t:{task.id}:0".encode(),
            )
        )
    if top_row:
        rows.append(top_row)

    retry_row: list[Any] = []
    if failed:
        retry_row.append(
            Button.inline(
                tr(lang, f"🔁 Retry Failed ({failed})", f"🔁 重试失败项 ({failed})"),
                f"tk:r:{task.id}".encode(),
            )
        )
    if blocked:
        label = (
            tr(lang, f"🔁 Retry All ({problems})", f"🔁 重试失败+阻断 ({problems})")
            if failed
            else tr(lang, f"🔁 Retry Blocked ({blocked})", f"🔁 重试阻断项 ({blocked})")
        )
        retry_row.append(Button.inline(label, f"tk:rb:{task.id}".encode()))
    if retry_row:
        rows.append(retry_row)

    rows.append(
        [
            Button.inline(tr(lang, "🔄 Refresh", "🔄 刷新"), f"tk:v:{task.id}".encode()),
            back_button(b"m:tasks", lang=lang),
        ]
    )
    return "\n".join(lines), rows


async def errors_view(ctx: AppContext, *, task_id: int | None = None, page: int = 0) -> tuple[str, list[list[Any]]]:
    """Paginated pure-text list of failed and blocked-skipped jobs (global or per-task)."""

    lang = ctx.lang
    scope = f"t:{task_id}" if task_id is not None else "g"
    back_cb = f"tk:v:{task_id}".encode() if task_id is not None else b"m:status"
    back_label = tr(lang, f"⬅️ Task #{task_id}", f"⬅️ 返回任务 #{task_id}") if task_id is not None else None

    counts = await ctx.repo.count_problem_jobs(task_id=task_id)
    jobs = await ctx.repo.list_problem_jobs(task_id=task_id, limit=200)
    if not jobs:
        title = (
            tr(lang, f"<b>✅ Task #{task_id} — No Issues</b>", f"<b>✅ 任务 #{task_id} 暂无异常项</b>")
            if task_id is not None
            else tr(lang, "<b>✅ No Failed or Blocked Items</b>", "<b>✅ 暂无失败或异常阻断记录</b>")
        )
        nav_btns = [[back_button(back_cb, back_label, lang=lang)]]
        if task_id is None:
            nav_btns.append([back_button(b"m:home", lang=lang)])
        return title, nav_btns

    target_map = {t.id: t.name for t in await ctx.repo.list_targets()}
    page_items, page, total_pages = paginate_items(jobs, page, PAGE_SIZE)
    page_suffix = f" ({page + 1}/{total_pages})" if total_pages > 1 else ""
    if task_id is not None:
        header = tr(
            lang,
            f"<b>⚠️ Task #{task_id} Issues{page_suffix}</b>\n❌Failed {counts['failed']} · ⚠️Blocked {counts['blocked']}",
            f"<b>⚠️ 任务 #{task_id} 异常明细{page_suffix}</b>\n❌失败 {counts['failed']} · ⚠️异常阻断 {counts['blocked']}",
        )
    else:
        header = tr(
            lang,
            f"<b>⚠️ Failed &amp; Blocked Items{page_suffix}</b>\n❌Failed {counts['failed']} · ⚠️Blocked {counts['blocked']}",
            f"<b>⚠️ 失败与异常阻断明细{page_suffix}</b>\n❌失败 {counts['failed']} · ⚠️异常阻断 {counts['blocked']}",
        )

    lines = [header]
    for job in page_items:
        icon = "❌" if job.status == "failed" else "⚠️"
        ids = [int(x) for x in (job.source_message_ids or []) if str(x).isdigit()]
        msg_span = f"{min(ids)}-{max(ids)}" if len(ids) > 1 else f"{job.source_message_id}"
        src_title = esc(job.source_title or job.source_chat_id)
        link = source_link(job.source_chat_id, int(job.source_message_id), job.source_topic_id)
        msg_ref = f'<a href="{esc(link)}">{src_title} #{msg_span}</a>' if link else f"{src_title} <code>#{msg_span}</code>"
        origin = ""
        if task_id is None:
            if job.task_id:
                origin = f"[T#{job.task_id}] "
            elif job.route_id:
                origin = f"[R#{job.route_id}] "
        tname = esc(target_map.get(job.target_id, f"#{job.target_id}"))
        size_part = f" · {format_bytes(job.file_size)}" if job.file_size else ""
        r_label = esc(reason_label(job.reason_code, lang))
        raw_err = " ".join((job.last_error or "").split())
        if len(raw_err) > 110:
            raw_err = raw_err[:109] + "…"
        err_part = f": <code>{esc(raw_err)}</code>" if raw_err else ""
        lines.append(
            f"• {icon} {origin}{msg_ref} → <code>#{job.target_id}</code> {tname} ({esc(job.media_type)}{size_part})\n"
            f"    └ <b>{r_label}</b>{err_part}"
        )

    rows: list[list[Any]] = []
    nav = pagination_row(f"err:{scope}", page, total_pages, lang=lang)
    if nav:
        rows.append(nav)

    retry_row: list[Any] = []
    failed_n = counts["failed"]
    blocked_n = counts["blocked"]
    total_n = counts["total"]
    if failed_n:
        retry_row.append(
            Button.inline(
                tr(lang, f"🔁 Retry Failed ({failed_n})", f"🔁 重试失败项 ({failed_n})"),
                f"err:rf:{scope}:{page}".encode(),
            )
        )
    if blocked_n:
        label = (
            tr(lang, f"🔁 Retry All ({total_n})", f"🔁 重试失败+阻断 ({total_n})")
            if failed_n
            else tr(lang, f"🔁 Retry Blocked ({blocked_n})", f"🔁 重试阻断项 ({blocked_n})")
        )
        retry_row.append(Button.inline(label, f"err:rb:{scope}:{page}".encode()))
    if retry_row:
        rows.append(retry_row)

    csv_scope = str(task_id) if task_id is not None else "0"
    rows.append(
        [
            Button.inline(tr(lang, "🔄 Refresh", "🔄 刷新"), f"err:{scope}:{page}".encode()),
            Button.inline(tr(lang, "📄 Export CSV", "📄 导出 CSV"), f"err:csv:{csv_scope}".encode()),
        ]
    )
    rows.append([back_button(back_cb, back_label, lang=lang)])
    if task_id is None:
        rows.append([back_button(b"m:home", lang=lang)])
    return "\n".join(lines), rows


# ---------------------------------------------------------------- link flow


def _selection_view(
    ctx: AppContext,
    token: str,
    selection: PendingSelection,
    targets: list[Any],
    page: int = 0,
) -> tuple[str, list[list[Any]]]:
    lang = ctx.lang
    count = selection.end - selection.start + 1
    span = (
        f"{selection.start}"
        if selection.start == selection.end
        else tr(lang, f"{selection.start}–{selection.end} ({count} IDs)", f"{selection.start}–{selection.end}（{count} 个 ID）")
    )
    topic = f"\nTopic: {selection.topic_id}" if selection.topic_id else ""
    page_items, page, total_pages = paginate_items(targets, page, PAGE_SIZE)
    selection.page = page
    page_suffix = f" ({page + 1}/{total_pages})" if total_pages > 1 else ""
    target_lines = "\n".join(f"• {esc(target_label(t, lang))}" for t in page_items)
    text = tr(
        lang,
        f"<b>📥 New Transfer Task</b>\n"
        f"Source: <code>{esc(selection.chat)}</code>{topic}\n"
        f"Messages: {span}\n\n"
        f"<b>Available Targets{page_suffix}:</b>\n{target_lines}\n\n"
        f"💬 Reply with target ID(s) (comma-separated, e.g. <code>1</code> or <code>1, 2</code>) to start immediately:",
        f"<b>📥 新搬运任务</b>\n"
        f"来源：<code>{esc(selection.chat)}</code>{topic}\n"
        f"消息：{span}\n\n"
        f"<b>可用目的地{page_suffix}：</b>\n{target_lines}\n\n"
        f"💬 请直接回复目的地编号（多个用逗号分隔，如 <code>1</code> 或 <code>1, 2</code>）立即启动任务：",
    )
    rows: list[list[Any]] = []
    nav = pagination_row(f"sel:{token}:p", page, total_pages, lang=lang)
    if nav:
        rows.append(nav)
    rows.append([Button.inline(tr(lang, "✖️ Cancel", "✖️ 取消"), f"sel:{token}:x".encode())])
    return text, rows


async def _start_task_from_selection(
    ctx: AppContext,
    selection: PendingSelection,
    target_ids: list[int],
    chat_id: int | None,
    respond_fn: Any,
) -> Any:
    lang = ctx.lang
    task = await ctx.repo.create_task(
        kind="adhoc",
        source_chat_id=selection.chat,
        source_topic_id=selection.topic_id,
        start_id=selection.start,
        end_id=selection.end,
        target_ids=sorted(set(target_ids)),
        created_by=selection.user_id,
        notify_chat_id=chat_id,
        status="scanning",
    )
    msg = await respond_fn(tr(lang, f"🔎 Scanning task #{task.id}…", f"🔎 任务 #{task.id} 扫描中…"))
    msg_id = getattr(msg, "id", None)
    launch_scan(ctx, task.id, chat_id, msg_id)
    return task


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
    ctx.state.set_selection(token, selection)
    text, buttons = _selection_view(ctx, token, selection, targets, page=0)
    await event.respond(text, buttons=buttons)


async def handle_selection_text(event: Any, ctx: AppContext) -> bool:
    """Consume a private text reply if the user has a pending link target selection."""

    entry = ctx.state.get_user_selection(event.sender_id)
    if entry is None:
        return False
    token, selection = entry
    raw = (event.raw_text or "").strip()
    # If the user sends a new Telegram link instead of target IDs, let router.py treat it as a new link request.
    if "t.me/" in raw or raw.startswith("tg://"):
        return False
    lang = ctx.lang
    cancel_btn = [[Button.inline(tr(lang, "✖️ Cancel", "✖️ 取消"), f"sel:{token}:x".encode())]]
    try:
        chosen_ids = target_id_list(raw)
    except ValueError as exc:
        await event.respond(
            tr(
                lang,
                f"⚠️ {esc(exc)}\nPlease reply with target ID(s) (e.g. <code>1</code> or <code>1, 2</code>):",
                f"⚠️ {esc(exc)}\n请回复目的地编号（如 <code>1</code> 或 <code>1, 2</code>）：",
            ),
            buttons=cancel_btn,
        )
        return True

    targets = await ctx.repo.list_targets(enabled_only=True)
    valid_ids = {t.id for t in targets}
    missing = [tid for tid in chosen_ids if tid not in valid_ids]
    if missing:
        missing_str = ", ".join(f"#{tid}" for tid in missing)
        await event.respond(
            tr(
                lang,
                f"⚠️ Enabled target(s) not found: <code>{esc(missing_str)}</code>.\nPlease reply with valid target ID(s):",
                f"⚠️ 未找到可用的目的地：<code>{esc(missing_str)}</code>。\n请重新回复有效的目的地编号：",
            ),
            buttons=cancel_btn,
        )
        return True

    ctx.state.pop_selection(token)
    await _start_task_from_selection(ctx, selection, chosen_ids, event.chat_id, event.respond)
    return True


async def on_selection(event: Any, ctx: AppContext) -> None:
    lang = ctx.lang
    parts = event.data.decode().split(":")
    token, action = parts[1], parts[2]
    selection = ctx.state.selections.get(token)
    if selection is None or selection.user_id != event.sender_id:
        await event.answer(tr(lang, "Selection expired. Please resend the link.", "该选择已过期，请重新发送链接"), alert=True)
        return
    if action == "x":
        ctx.state.pop_selection(token)
        await event.edit(
            tr(lang, "✖️ Cancelled.", "✖️ 已取消。"),
            buttons=[[back_button(b"m:home", lang=lang)]],
        )
        return
    if action == "p":
        page = int(parts[3]) if len(parts) > 3 and parts[3].lstrip("-").isdigit() else 0
        targets = await ctx.repo.list_targets(enabled_only=True)
        text, buttons = _selection_view(ctx, token, selection, targets, page=page)
        await event.edit(text, buttons=buttons)
        return
    if action == "go":
        if not selection.selected:
            await event.answer(tr(lang, "Please select at least one target", "请至少选择一个目的地"), alert=True)
            return
        ctx.state.pop_selection(token)
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
    text, buttons = _selection_view(ctx, token, selection, targets, page=selection.page)
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
                text += tr(
                    lang,
                    f"\nYou will be notified when complete. Send <code>/tasks {task_id}</code> for progress.",
                    f"\n完成后会通知你。发送 <code>/tasks {task_id}</code> 查看进度。",
                )
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
    admin_ids = getattr(getattr(ctx, "boot", None), "admin_ids", None) or (event.sender_id,)
    await sync_bot_commands(getattr(getattr(ctx, "clients", None), "bot", None), ctx.lang, admin_ids)
    text, buttons = await start_view(ctx)
    await event.respond(text, buttons=buttons)


async def cmd_help(event: Any, ctx: AppContext) -> None:
    await event.respond(
        help_text(ctx.lang),
        buttons=[[back_button(b"m:home", lang=ctx.lang)]],
    )


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
    admin_ids = getattr(getattr(ctx, "boot", None), "admin_ids", None) or (event.sender_id,)
    await sync_bot_commands(getattr(getattr(ctx, "clients", None), "bot", None), new_lang, admin_ids)
    msg = "🌐 语言已切换为：<b>简体中文</b>" if new_lang == "zh" else "🌐 Language switched to: <b>English</b>"
    text, buttons = await start_view(ctx)
    await event.respond(msg + "\n\n" + text, buttons=buttons)


async def cmd_status(event: Any, ctx: AppContext) -> None:
    text, buttons = await status_view(ctx)
    await event.respond(text, buttons=buttons)


async def cmd_tasks(event: Any, ctx: AppContext) -> None:
    arg = command_args(event).lstrip("#").strip()
    if arg:
        if not arg.isdigit():
            await event.respond(
                ctx.t(
                    "Usage: /tasks <code>[task_id]</code>",
                    "用法：/tasks <code>[任务ID]</code>",
                )
            )
            return
        text, buttons = await task_detail_view(ctx, int(arg))
        await event.respond(text, buttons=buttons)
        return
    text, buttons = await tasks_view(ctx, page=0)
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


async def cmd_errors(event: Any, ctx: AppContext) -> None:
    arg = command_args(event).lstrip("#").strip()
    task_id: int | None = None
    if arg:
        if not arg.isdigit():
            await event.respond(
                ctx.t(
                    "Usage: /errors <code>[task_id]</code>",
                    "用法：/errors <code>[任务ID]</code>",
                )
            )
            return
        task_id = int(arg)
    text, buttons = await errors_view(ctx, task_id=task_id, page=0)
    await event.respond(text, buttons=buttons)


async def cmd_retry(event: Any, ctx: AppContext) -> None:
    tokens = [tok.lstrip("#").lower() for tok in command_args(event).split() if tok.strip()]
    task_id: int | None = None
    include_blocked = False
    for tok in tokens:
        if tok.isdigit() and task_id is None:
            task_id = int(tok)
        elif tok in {"all", "blocked", "skipped"}:
            include_blocked = True
        else:
            await event.respond(
                ctx.t(
                    "Usage: /retry <code>[task_id] [all]</code>\nAdd <code>all</code> to also re-queue blocked skips (permission/oversized/etc.).",
                    "用法：/retry <code>[任务ID] [all]</code>\n附加 <code>all</code> 可将异常阻断跳过项（权限不足/超限等）一并重新入队。",
                )
            )
            return
    count = await ctx.repo.retry_jobs(task_id=task_id, include_blocked=include_blocked)
    ctx.worker.wake()
    if include_blocked:
        await event.respond(
            ctx.t(
                f"🔁 Re-queued {count} failed & blocked item(s).",
                f"🔁 已重新排队 {count} 个失败与异常阻断项。",
            )
        )
    else:
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


async def _send_report(event: Any, ctx: AppContext, *, task_id: int | None = None) -> None:
    rows, content = await problem_report_csv(ctx.repo, task_id=task_id)
    if not rows:
        await event.respond(
            ctx.t(
                "No failed, blocked, or oversized records found.",
                "没有失败、异常阻断或超限文件记录。",
            )
        )
        return
    filename = f"report_task_{task_id}.csv" if task_id is not None else "problem_report.csv"
    caption = (
        ctx.t(f"📄 Task #{task_id} issue report: {rows} row(s)", f"📄 任务 #{task_id} 异常与超限报表：{rows} 条")
        if task_id is not None
        else ctx.t(f"📄 Issue & oversized report: {rows} row(s)", f"📄 全局异常与超限报表：{rows} 条")
    )
    await event.respond(caption, file=_named_bytes(content, filename))


async def cmd_report(event: Any, ctx: AppContext) -> None:
    arg = command_args(event).lstrip("#").strip()
    task_id: int | None = None
    if arg:
        if not arg.isdigit():
            await event.respond(ctx.t("Usage: /report <code>[task_id]</code>", "用法：/report <code>[任务ID]</code>"))
            return
        task_id = int(arg)
    await _send_report(event, ctx, task_id=task_id)


def _named_bytes(content: bytes, name: str) -> Any:
    import io

    buffer = io.BytesIO(content)
    buffer.name = name
    return buffer


# ---------------------------------------------------------------- callbacks


async def on_menu(event: Any, ctx: AppContext) -> None:
    parts = event.data.decode().split(":")
    action = parts[1]
    if action == "home":
        text, buttons = await start_view(ctx)
        await event.edit(text, buttons=buttons)
        return
    if action == "help":
        await event.edit(
            help_text(ctx.lang),
            buttons=[[back_button(b"m:home", lang=ctx.lang)]],
        )
        return
    if action == "lang":
        new_lang = "zh" if ctx.lang == "en" else "en"
        await ctx.store.set("ui.language", new_lang)
        admin_ids = getattr(getattr(ctx, "boot", None), "admin_ids", None) or (event.sender_id,)
        await sync_bot_commands(getattr(getattr(ctx, "clients", None), "bot", None), new_lang, admin_ids)
        await event.answer("已切换为简体中文" if new_lang == "zh" else "Switched to English")
        text, buttons = await start_view(ctx)
        await event.edit(text, buttons=buttons)
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
        page = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else 0
        text, buttons = await tasks_view(ctx, page=page)
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
    elif action == "rb":
        count = await ctx.repo.retry_jobs(task_id=task_id, include_blocked=True)
        ctx.worker.wake()
        await event.answer(ctx.t(f"Re-queued {count} item(s)", f"已重新排队 {count} 项"))
    text, buttons = await task_detail_view(ctx, task_id)
    await event.edit(text, buttons=buttons)


async def on_error_button(event: Any, ctx: AppContext) -> None:
    parts = event.data.decode().split(":")
    op = parts[1]
    if op == "csv":
        raw_tid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await event.answer()
        await _send_report(event, ctx, task_id=raw_tid or None)
        return

    retry_mode: str | None = None
    idx = 1
    if op in {"rf", "rb"}:
        retry_mode = op
        idx = 2

    scope_kind = parts[idx] if len(parts) > idx else "g"
    if scope_kind == "t" and len(parts) > idx + 1 and parts[idx + 1].isdigit():
        task_id: int | None = int(parts[idx + 1])
        page_idx = idx + 2
    else:
        task_id = None
        page_idx = idx + 1
    page = int(parts[page_idx]) if len(parts) > page_idx and parts[page_idx].lstrip("-").isdigit() else 0

    if retry_mode is not None:
        count = await ctx.repo.retry_jobs(task_id=task_id, include_blocked=(retry_mode == "rb"))
        ctx.worker.wake()
        await event.answer(ctx.t(f"Re-queued {count} item(s)", f"已重新排队 {count} 项"))

    text, buttons = await errors_view(ctx, task_id=task_id, page=page)
    await event.edit(text, buttons=buttons)


def register(bot: TelegramClient, ctx: AppContext) -> None:
    commands = {
        "start": cmd_start,
        "help": cmd_help,
        "lang": cmd_lang,
        "status": cmd_status,
        "tasks": cmd_tasks,
        "task": cmd_tasks,
        "errors": cmd_errors,
        "failed": cmd_errors,
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
    bot.add_event_handler(admin_only(ctx, on_error_button), events.CallbackQuery(pattern=rb"^err:"))
