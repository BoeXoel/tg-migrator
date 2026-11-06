"""Settings panel, /set and /get, content filters, YAML export/import."""

from __future__ import annotations

import io
import logging
from typing import Any

import yaml
from telethon import Button, TelegramClient, events

from app.backup import export_yaml, import_yaml
from app.bot.auth import admin_only
from app.bot.context import AppContext
from app.bot.ui import KIND_LABELS, back_button, chunk_buttons, command, command_args, esc, onoff
from app.config import CHOICES, SECTION_NAMES, dataclass_to_dict, get_setting, leaf_paths
from app.engine.filters import validate_rule

log = logging.getLogger("migrator.bot.config")

TRANSFER_TOGGLES = {
    "transfer.hide_sender": "隐藏来源（复制而非转发）",
    "transfer.drop_caption": "去除配文",
    "transfer.strip_spoilers": "移除剧透遮罩",
    "transfer.native_copy": "优先原生复制（免下载）",
    "transfer.native_only": "仅原生复制（不下载重传）",
    "transfer.allow_download_unknown_size": "允许下载未知大小文件",
    "transfer.allow_premium_user_fallback": "Premium 账号代发超大文件",
}
ARCHIVE_TOGGLES = {
    "archive.group_by_month": "按月分目录（YYYY-MM）",
    "archive.sidecar_always": "无配文媒体也写元数据",
}
CYCLES = {
    "transfer.caption_overflow": ("超长配文", {"split": "拆分为单独消息", "truncate": "截断"}),
    "archive.sidecar_format": ("元数据格式", {"json": "JSON", "md": "Markdown", "both": "JSON + MD", "none": "不生成"}),
    "archive.folder_naming": ("来源目录名", {"title_id": "名称_ID", "id": "仅 ID", "title": "仅名称"}),
    "archive.webdav_mode": ("WebDAV 传输", {"auto": "自动", "stream": "流式（不落盘）", "spool": "先落盘"}),
}


def _toggle_button(ctx: AppContext, path: str, label: str) -> Any:
    return Button.inline(f"{onoff(bool(get_setting(ctx.store.current, path)))} {label}", f"cfg:t:{path}".encode())


def _cycle_button(ctx: AppContext, path: str) -> Any:
    label, options = CYCLES[path]
    value = get_setting(ctx.store.current, path)
    return Button.inline(f"🔀 {label}：{options.get(value, value)}", f"cfg:c:{path}".encode())


def config_view(ctx: AppContext, section: str = "main") -> tuple[str, list[list[Any]]]:
    settings = ctx.store.current
    if section == "media":
        buttons = [
            _toggle_button(ctx, f"transfer.include.{name}", label) for name, label in KIND_LABELS.items()
        ]
        rows = chunk_buttons(buttons, 2)
        rows.append([back_button(b"cfg:main")])
        return "<b>🎞 搬运的消息类型</b>", rows
    if section == "transfer":
        rows = [[_toggle_button(ctx, path, label)] for path, label in TRANSFER_TOGGLES.items()]
        rows.append([_cycle_button(ctx, "transfer.caption_overflow")])
        rows.append([back_button(b"cfg:main")])
        limit = settings.transfer.max_upload_bytes
        text = (
            "<b>🚚 传输策略</b>\n"
            f"上传上限：{'自动（按账号）' if not limit else f'{limit} 字节'}（/set transfer.max_upload_bytes 值）"
        )
        return text, rows
    if section == "archive":
        rows = [[_toggle_button(ctx, path, label)] for path, label in ARCHIVE_TOGGLES.items()]
        rows.extend([[_cycle_button(ctx, path)] for path in ("archive.sidecar_format", "archive.folder_naming", "archive.webdav_mode")])
        rows.append([back_button(b"cfg:main")])
        text = (
            "<b>🗄 归档（本地 / WebDAV）</b>\n"
            "路径：<code>来源/[topic_ID/][YYYY-MM/]消息ID_文件名</code>\n"
            "纯文本与配文写入同目录的元数据文件。"
        )
        return text, rows
    if section == "filters":
        rows = [
            [_toggle_button(ctx, "filters.enabled", "启用过滤")],
            [_toggle_button(ctx, "filters.case_sensitive", "区分大小写")],
            [Button.inline("📃 查看规则", b"cfg:rules"), back_button(b"cfg:main")],
        ]
        return "<b>🧹 内容过滤</b>\n命中关键词/正则的文本或配文会让整条消息（含相册）被跳过。", rows
    if section == "advanced":
        lines = ["<b>🛠 高级参数</b>（/set 设置项 值）"]
        for name in ("limits", "worker", "queue", "watch", "downloads"):
            for path in leaf_paths():
                if path.startswith(name + "."):
                    lines.append(f"<code>{esc(path)}</code> = {esc(get_setting(settings, path))}")
        return "\n".join(lines), [[back_button(b"cfg:main")]]
    rows = [
        [Button.inline("🎞 消息类型", b"cfg:s:media"), Button.inline("🚚 传输策略", b"cfg:s:transfer")],
        [Button.inline("🗄 归档", b"cfg:s:archive"), Button.inline("🧹 过滤", b"cfg:s:filters")],
        [Button.inline("🛠 高级参数", b"cfg:s:advanced"), Button.inline("💾 导出 YAML", b"cfg:export")],
    ]
    return "<b>⚙️ 设置</b>\n修改即时生效并保存到数据库。", rows


async def rules_text(ctx: AppContext) -> str:
    rules = await ctx.repo.list_filters()
    if not rules:
        return "暂无过滤规则。\n/filter_add keyword 关键词\n/filter_add regex 正则"
    lines = ["<b>过滤规则</b>"]
    for rule in rules:
        lines.append(f"#{rule.id} {'✅' if rule.enabled else '⬜'} {rule.rule_type}: <code>{esc(rule.pattern)}</code>")
    lines.append("\n/filter_del ID 删除 · /filter_toggle ID 启停")
    return "\n".join(lines)


async def cmd_config(event: Any, ctx: AppContext) -> None:
    text, buttons = config_view(ctx)
    await event.respond(text, buttons=buttons)


async def cmd_set(event: Any, ctx: AppContext) -> None:
    parts = command_args(event).split(None, 1)
    if len(parts) < 2:
        await event.respond("用法：/set 设置项 值\n例：<code>/set worker.batch_size 50</code>\n查看全部：/get")
        return
    try:
        await ctx.store.set(parts[0], parts[1])
    except (KeyError, ValueError, TypeError) as exc:
        hint = f"\n可选值：{', '.join(CHOICES[parts[0]])}" if parts[0] in CHOICES else ""
        await event.respond(f"❌ {esc(exc)}{esc(hint)}")
        return
    ctx.worker.wake()
    await event.respond(f"✅ <code>{esc(parts[0])}</code> = {esc(get_setting(ctx.store.current, parts[0]))}")


async def cmd_get(event: Any, ctx: AppContext) -> None:
    section = command_args(event)
    data = dataclass_to_dict(ctx.store.current)
    if section:
        if section not in SECTION_NAMES:
            await event.respond(f"分区：{', '.join(SECTION_NAMES)}")
            return
        data = {section: data[section]}
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    await event.respond(f"<pre>{esc(text[:3800])}</pre>")


async def cmd_filters(event: Any, ctx: AppContext) -> None:
    await event.respond(await rules_text(ctx))


async def cmd_filter_add(event: Any, ctx: AppContext) -> None:
    parts = command_args(event).split(None, 1)
    if len(parts) < 2:
        await event.respond("用法：/filter_add keyword|regex 内容")
        return
    rule_type, pattern = parts[0].lower(), parts[1]
    try:
        validate_rule(rule_type, pattern)
    except ValueError as exc:
        await event.respond(f"❌ {esc(exc)}")
        return
    rule = await ctx.repo.add_filter(rule_type, pattern)
    note = "" if ctx.store.current.filters.enabled else "\n⚠️ 过滤当前未启用：/set filters.enabled true"
    await event.respond(f"✅ 已添加规则 #{rule.id}{note}")


async def cmd_filter_del(event: Any, ctx: AppContext) -> None:
    raw = command_args(event).lstrip("#")
    if not raw.isdigit():
        await event.respond("用法：/filter_del ID")
        return
    ok = await ctx.repo.delete_filter(int(raw))
    await event.respond("🗑 已删除。" if ok else "规则不存在。")


async def cmd_filter_toggle(event: Any, ctx: AppContext) -> None:
    raw = command_args(event).lstrip("#")
    rules = {rule.id: rule for rule in await ctx.repo.list_filters()}
    if not raw.isdigit() or int(raw) not in rules:
        await event.respond("用法：/filter_toggle ID")
        return
    rule = rules[int(raw)]
    await ctx.repo.set_filter_enabled(rule.id, not rule.enabled)
    await event.respond(f"规则 #{rule.id} 已{'停用' if rule.enabled else '启用'}。")


async def _send_export(event: Any, ctx: AppContext, include_secrets: bool) -> None:
    content = await export_yaml(ctx.repo, ctx.store, include_secrets=include_secrets)
    buffer = io.BytesIO(content)
    buffer.name = "tg-migrator-backup.yaml"
    caption = "💾 配置备份" + ("\n⚠️ 含明文密码，请妥善保管并删除本消息。" if include_secrets else "（密码已脱敏）")
    await event.respond(caption, file=buffer)


async def cmd_export(event: Any, ctx: AppContext) -> None:
    await _send_export(event, ctx, command_args(event).lower() in {"secrets", "full"})


async def cmd_import(event: Any, ctx: AppContext) -> None:
    mode = "replace" if command_args(event).lower() == "replace" else "merge"
    if event.message.document:
        await handle_import_file(event, ctx, mode)
        return
    ctx.state.import_modes[event.sender_id] = mode
    warning = "\n⚠️ replace 模式会删除文件中不存在的目的地与路由（含其队列记录）。" if mode == "replace" else ""
    await event.respond(f"请发送 YAML 备份文件（模式：{mode}）。{warning}\n/cancel 取消")


async def handle_import_file(event: Any, ctx: AppContext, mode: str | None = None) -> None:
    document = event.message.document
    if document is None or document.size > 2 * 1024 * 1024:
        await event.respond("❌ 请发送不超过 2 MB 的 YAML 文件。")
        return
    mode = mode or ctx.state.import_modes.pop(event.sender_id, "merge")
    content = await event.message.download_media(file=bytes)
    try:
        report = await import_yaml(content, ctx.repo, ctx.store, archive_root=ctx.boot.archive_root, replace=mode == "replace")
    except ValueError as exc:
        await event.respond(f"❌ 导入失败，未做任何修改：{esc(exc)}")
        return
    await ctx.watcher.refresh()
    ctx.worker.wake()
    await event.respond(f"✅ 导入完成（{mode}）\n{esc(report.summary())}")


async def on_config_button(event: Any, ctx: AppContext) -> None:
    parts = event.data.decode().split(":", 2)
    action = parts[1]
    section = "main"
    if action == "s":
        section = parts[2]
    elif action == "t":
        path = parts[2]
        await ctx.store.set(path, not bool(get_setting(ctx.store.current, path)))
        section = _section_for(path)
    elif action == "c":
        path = parts[2]
        options = list(CYCLES[path][1])
        current = get_setting(ctx.store.current, path)
        await ctx.store.set(path, options[(options.index(current) + 1) % len(options)] if current in options else options[0])
        section = _section_for(path)
    elif action == "rules":
        await event.edit(await rules_text(ctx), buttons=[[back_button(b"cfg:s:filters")]])
        return
    elif action == "export":
        await event.answer()
        await _send_export(event, ctx, False)
        return
    text, buttons = config_view(ctx, section)
    await event.edit(text, buttons=buttons)


def _section_for(path: str) -> str:
    if path.startswith("transfer.include."):
        return "media"
    return path.split(".", 1)[0] if path.split(".", 1)[0] in {"transfer", "archive", "filters"} else "main"


def register(bot: TelegramClient, ctx: AppContext) -> None:
    commands = {
        "config": cmd_config,
        "set": cmd_set,
        "get": cmd_get,
        "filters": cmd_filters,
        "filter_add": cmd_filter_add,
        "filter_del": cmd_filter_del,
        "filter_toggle": cmd_filter_toggle,
        "export": cmd_export,
        "import": cmd_import,
    }
    for name, handler in commands.items():
        bot.add_event_handler(admin_only(ctx, handler), events.NewMessage(pattern=command(name)))
    bot.add_event_handler(admin_only(ctx, on_config_button), events.CallbackQuery(pattern=rb"^cfg:"))
