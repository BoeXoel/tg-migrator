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
from app.bot.ui import (
    PAGE_SIZE,
    back_button,
    chunk_buttons,
    command,
    command_args,
    esc,
    kind_labels,
    onoff,
    paginate_items,
    pagination_row,
    sync_bot_commands,
    tr,
)
from app.config import CHOICES, SECTION_NAMES, dataclass_to_dict, get_setting, leaf_paths
from app.engine.filters import validate_rule

log = logging.getLogger("migrator.bot.config")

TRANSFER_TOGGLES_I18N: dict[str, dict[str, str]] = {
    "en": {
        "transfer.hide_sender": "Hide sender (copy instead of forward)",
        "transfer.drop_caption": "Strip captions",
        "transfer.strip_spoilers": "Remove spoiler masks",
        "transfer.native_copy": "Prefer native copy (zero download)",
        "transfer.native_only": "Native copy only (never download/re-upload)",
        "transfer.allow_download_unknown_size": "Allow downloading unknown-size files",
        "transfer.allow_premium_user_fallback": "Use Premium reader for 2–4 GiB files",
    },
    "zh": {
        "transfer.hide_sender": "隐藏来源（复制而非转发）",
        "transfer.drop_caption": "去除配文",
        "transfer.strip_spoilers": "移除剧透遮罩",
        "transfer.native_copy": "优先原生复制（免下载）",
        "transfer.native_only": "仅原生复制（不下载重传）",
        "transfer.allow_download_unknown_size": "允许下载未知大小文件",
        "transfer.allow_premium_user_fallback": "Premium 账号代发超大文件",
    },
}

ARCHIVE_TOGGLES_I18N: dict[str, dict[str, str]] = {
    "en": {
        "archive.group_by_month": "Group by month (YYYY-MM/)",
        "archive.sidecar_always": "Write sidecar even without caption",
    },
    "zh": {
        "archive.group_by_month": "按月分目录（YYYY-MM）",
        "archive.sidecar_always": "无配文媒体也写元数据",
    },
}

CYCLES_I18N: dict[str, dict[str, tuple[str, dict[str, str]]]] = {
    "en": {
        "transfer.caption_overflow": ("Long caption", {"split": "Split message", "truncate": "Truncate"}),
        "archive.sidecar_format": ("Sidecar format", {"json": "JSON", "md": "Markdown", "both": "JSON + MD", "none": "None"}),
        "archive.folder_naming": ("Source folder", {"title_id": "Title_ID", "id": "ID only", "title": "Title only"}),
        "archive.webdav_mode": ("WebDAV mode", {"auto": "Auto", "stream": "Stream (zero-disk)", "spool": "Spool to disk"}),
    },
    "zh": {
        "transfer.caption_overflow": ("超长配文", {"split": "拆分为单独消息", "truncate": "截断"}),
        "archive.sidecar_format": ("元数据格式", {"json": "JSON", "md": "Markdown", "both": "JSON + MD", "none": "不生成"}),
        "archive.folder_naming": ("来源目录名", {"title_id": "名称_ID", "id": "仅 ID", "title": "仅名称"}),
        "archive.webdav_mode": ("WebDAV 传输", {"auto": "自动", "stream": "流式（不落盘）", "spool": "先落盘"}),
    },
}

# Backwards-compatible aliases
TRANSFER_TOGGLES = TRANSFER_TOGGLES_I18N["en"]
ARCHIVE_TOGGLES = ARCHIVE_TOGGLES_I18N["en"]
CYCLES = CYCLES_I18N["en"]


def _toggle_button(ctx: AppContext, path: str, label: str) -> Any:
    return Button.inline(f"{onoff(bool(get_setting(ctx.store.current, path)))} {label}", f"cfg:t:{path}".encode())


def _cycle_button(ctx: AppContext, path: str) -> Any:
    lang = ctx.lang
    cycles = CYCLES_I18N.get(lang, CYCLES_I18N["en"])
    label, options = cycles[path]
    value = get_setting(ctx.store.current, path)
    sep = "：" if lang == "zh" else ": "
    return Button.inline(f"🔀 {label}{sep}{options.get(value, value)}", f"cfg:c:{path}".encode())


def config_view(ctx: AppContext, section: str = "main") -> tuple[str, list[list[Any]]]:
    lang = ctx.lang
    settings = ctx.store.current
    if section == "media":
        labels = kind_labels(lang)
        buttons = [
            _toggle_button(ctx, f"transfer.include.{name}", label) for name, label in labels.items()
        ]
        rows = chunk_buttons(buttons, 2)
        rows.append([back_button(b"cfg:main", lang=lang)])
        return tr(lang, "<b>🎞 Included Message Types</b>", "<b>🎞 搬运的消息类型</b>"), rows
    if section == "transfer":
        toggles = TRANSFER_TOGGLES_I18N.get(lang, TRANSFER_TOGGLES_I18N["en"])
        rows = [[_toggle_button(ctx, path, label)] for path, label in toggles.items()]
        rows.append([_cycle_button(ctx, "transfer.caption_overflow")])
        rows.append([back_button(b"cfg:main", lang=lang)])
        limit = settings.transfer.max_upload_bytes
        limit_desc = (
            tr(lang, "Auto (per account)", "自动（按账号）")
            if not limit
            else tr(lang, f"{limit} bytes", f"{limit} 字节")
        )
        text = tr(
            lang,
            f"<b>🚚 Transfer Policy</b>\nUpload limit: {limit_desc} (<code>/set transfer.max_upload_bytes &lt;value&gt;</code>)",
            f"<b>🚚 传输策略</b>\n上传上限：{limit_desc}（<code>/set transfer.max_upload_bytes 值</code>）",
        )
        return text, rows
    if section == "archive":
        toggles = ARCHIVE_TOGGLES_I18N.get(lang, ARCHIVE_TOGGLES_I18N["en"])
        rows = [[_toggle_button(ctx, path, label)] for path, label in toggles.items()]
        rows.extend([[_cycle_button(ctx, path)] for path in ("archive.sidecar_format", "archive.folder_naming", "archive.webdav_mode")])
        rows.append([back_button(b"cfg:main", lang=lang)])
        text = tr(
            lang,
            "<b>🗄 Archive (Local / WebDAV)</b>\n"
            "Layout: <code>source/[topic_ID/][YYYY-MM/]msgID_filename</code>\n"
            "Plain text and media captions are written to a companion sidecar file.",
            "<b>🗄 归档（本地 / WebDAV）</b>\n"
            "路径：<code>来源/[topic_ID/][YYYY-MM/]消息ID_文件名</code>\n"
            "纯文本与配文写入同目录的元数据文件。",
        )
        return text, rows
    if section == "filters":
        rows = [
            [_toggle_button(ctx, "filters.enabled", tr(lang, "Enable content filtering", "启用过滤"))],
            [_toggle_button(ctx, "filters.case_sensitive", tr(lang, "Case-sensitive matching", "区分大小写"))],
            [Button.inline(tr(lang, "📃 View Rules", "📃 查看规则"), b"cfg:rules"), back_button(b"cfg:main", lang=lang)],
        ]
        return (
            tr(
                lang,
                "<b>🧹 Content Filters</b>\nMessages (including entire Albums) matching any keyword/regex rule are skipped.",
                "<b>🧹 内容过滤</b>\n命中关键词/正则的文本或配文会让整条消息（含相册）被跳过。",
            ),
            rows,
        )
    if section == "advanced":
        lines = [tr(lang, "<b>🛠 Advanced Parameters</b> (<code>/set key value</code>)", "<b>🛠 高级参数</b>（<code>/set 设置项 值</code>）")]
        for name in ("limits", "worker", "queue", "watch", "downloads", "ui"):
            for path in leaf_paths():
                if path.startswith(name + "."):
                    lines.append(f"<code>{esc(path)}</code> = {esc(get_setting(settings, path))}")
        return "\n".join(lines), [[back_button(b"cfg:main", lang=lang)]]
    rows = [
        [
            Button.inline(tr(lang, "🎞 Media Types", "🎞 消息类型"), b"cfg:s:media"),
            Button.inline(tr(lang, "🚚 Transfer", "🚚 传输策略"), b"cfg:s:transfer"),
        ],
        [
            Button.inline(tr(lang, "🗄 Archive", "🗄 归档"), b"cfg:s:archive"),
            Button.inline(tr(lang, "🧹 Filters", "🧹 过滤"), b"cfg:s:filters"),
        ],
        [
            Button.inline(tr(lang, "🛠 Advanced", "🛠 高级参数"), b"cfg:s:advanced"),
            Button.inline(tr(lang, "💾 Export YAML", "💾 导出 YAML"), b"cfg:export"),
        ],
        [
            Button.inline(tr(lang, "🌐 Language: English (Switch to 中文)", "🌐 语言：简体中文（切换为 English）"), b"cfg:lang"),
        ],
        [back_button(b"m:home", lang=lang)],
    ]
    return (
        tr(
            lang,
            "<b>⚙️ Settings</b>\nChanges take effect immediately and are persisted in the database.",
            "<b>⚙️ 设置</b>\n修改即时生效并保存到数据库。",
        ),
        rows,
    )


async def rules_view(ctx: AppContext, page: int = 0) -> tuple[str, list[list[Any]]]:
    lang = ctx.lang
    rules = await ctx.repo.list_filters()
    if not rules:
        return (
            tr(
                lang,
                "No filter rules configured.\n/filter_add keyword &lt;word&gt;\n/filter_add regex &lt;pattern&gt;",
                "暂无过滤规则。\n/filter_add keyword 关键词\n/filter_add regex 正则",
            ),
            [[back_button(b"cfg:s:filters", lang=lang)]],
        )
    page_items, page, total_pages = paginate_items(rules, page, PAGE_SIZE)
    page_suffix = f" ({page + 1}/{total_pages})" if total_pages > 1 else ""
    lines = [tr(lang, f"<b>🧹 Filter Rules{page_suffix}</b>", f"<b>🧹 过滤规则{page_suffix}</b>")]
    for rule in page_items:
        lines.append(f"• <code>#{rule.id}</code> {'✅' if rule.enabled else '⬜'} {rule.rule_type}: <code>{esc(rule.pattern)}</code>")
    lines.append(tr(lang, "\n/filter_del ID to delete · /filter_toggle ID to enable/disable", "\n/filter_del ID 删除 · /filter_toggle ID 启停"))
    rows: list[list[Any]] = []
    nav = pagination_row("cfg:rules", page, total_pages, lang=lang)
    if nav:
        rows.append(nav)
    rows.append([back_button(b"cfg:s:filters", lang=lang)])
    return "\n".join(lines), rows


async def rules_text(ctx: AppContext, page: int = 0) -> str:
    text, _ = await rules_view(ctx, page=page)
    return text


async def cmd_config(event: Any, ctx: AppContext) -> None:
    text, buttons = config_view(ctx)
    await event.respond(text, buttons=buttons)


async def cmd_set(event: Any, ctx: AppContext) -> None:
    lang = ctx.lang
    parts = command_args(event).split(None, 1)
    if len(parts) < 2:
        await event.respond(
            tr(
                lang,
                "Usage: /set &lt;key&gt; &lt;value&gt;\nExample: <code>/set worker.batch_size 50</code>\nView all: /get",
                "用法：/set 设置项 值\n例：<code>/set worker.batch_size 50</code>\n查看全部：/get",
            )
        )
        return
    try:
        await ctx.store.set(parts[0], parts[1])
    except (KeyError, ValueError, TypeError) as exc:
        choices_label = tr(lang, "Allowed values", "可选值")
        hint = f"\n{choices_label}: {', '.join(CHOICES[parts[0]])}" if parts[0] in CHOICES else ""
        await event.respond(f"❌ {esc(exc)}{esc(hint)}")
        return
    ctx.worker.wake()
    await event.respond(f"✅ <code>{esc(parts[0])}</code> = {esc(get_setting(ctx.store.current, parts[0]))}")


async def cmd_get(event: Any, ctx: AppContext) -> None:
    section = command_args(event)
    data = dataclass_to_dict(ctx.store.current)
    if section:
        if section not in SECTION_NAMES:
            await event.respond(ctx.t(f"Sections: {', '.join(SECTION_NAMES)}", f"分区：{', '.join(SECTION_NAMES)}"))
            return
        data = {section: data[section]}
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    await event.respond(f"<pre>{esc(text[:3800])}</pre>")


async def cmd_filters(event: Any, ctx: AppContext) -> None:
    arg = command_args(event).strip()
    page = max(0, int(arg) - 1) if arg.isdigit() else 0
    text, buttons = await rules_view(ctx, page=page)
    await event.respond(text, buttons=buttons)


async def cmd_filter_add(event: Any, ctx: AppContext) -> None:
    lang = ctx.lang
    parts = command_args(event).split(None, 1)
    if len(parts) < 2:
        await event.respond(tr(lang, "Usage: /filter_add keyword|regex &lt;pattern&gt;", "用法：/filter_add keyword|regex 内容"))
        return
    rule_type, pattern = parts[0].lower(), parts[1]
    try:
        validate_rule(rule_type, pattern)
    except ValueError as exc:
        await event.respond(f"❌ {esc(exc)}")
        return
    rule = await ctx.repo.add_filter(rule_type, pattern)
    note = (
        ""
        if ctx.store.current.filters.enabled
        else tr(
            lang,
            "\n⚠️ Content filtering is currently disabled: <code>/set filters.enabled true</code>",
            "\n⚠️ 过滤当前未启用：<code>/set filters.enabled true</code>",
        )
    )
    await event.respond(tr(lang, f"✅ Added rule #{rule.id}{note}", f"✅ 已添加规则 #{rule.id}{note}"))


async def cmd_filter_del(event: Any, ctx: AppContext) -> None:
    raw = command_args(event).lstrip("#")
    if not raw.isdigit():
        await event.respond(ctx.t("Usage: /filter_del <id>", "用法：/filter_del ID"))
        return
    ok = await ctx.repo.delete_filter(int(raw))
    await event.respond(ctx.t("🗑 Deleted." if ok else "Rule does not exist.", "🗑 已删除。" if ok else "规则不存在。"))


async def cmd_filter_toggle(event: Any, ctx: AppContext) -> None:
    raw = command_args(event).lstrip("#")
    rules = {rule.id: rule for rule in await ctx.repo.list_filters()}
    if not raw.isdigit() or int(raw) not in rules:
        await event.respond(ctx.t("Usage: /filter_toggle <id>", "用法：/filter_toggle ID"))
        return
    rule = rules[int(raw)]
    await ctx.repo.set_filter_enabled(rule.id, not rule.enabled)
    state_en = "disabled" if rule.enabled else "enabled"
    state_zh = "停用" if rule.enabled else "启用"
    await event.respond(ctx.t(f"Rule #{rule.id} {state_en}.", f"规则 #{rule.id} 已{state_zh}。"))


async def _send_export(event: Any, ctx: AppContext, include_secrets: bool) -> None:
    lang = ctx.lang
    content = await export_yaml(ctx.repo, ctx.store, include_secrets=include_secrets)
    buffer = io.BytesIO(content)
    buffer.name = "tg-migrator-backup.yaml"
    if lang == "zh":
        caption = "💾 配置备份" + ("\n⚠️ 含明文密码，请妥善保管并删除本消息。" if include_secrets else "（密码已脱敏）")
    else:
        caption = "💾 Configuration backup" + (
            "\n⚠️ Contains plaintext passwords; store securely and delete this message."
            if include_secrets
            else " (passwords redacted)"
        )
    await event.respond(caption, file=buffer)


async def cmd_export(event: Any, ctx: AppContext) -> None:
    await _send_export(event, ctx, command_args(event).lower() in {"secrets", "full"})


async def cmd_import(event: Any, ctx: AppContext) -> None:
    lang = ctx.lang
    mode = "replace" if command_args(event).lower() == "replace" else "merge"
    if event.message.document:
        await handle_import_file(event, ctx, mode)
        return
    ctx.state.import_modes[event.sender_id] = mode
    if lang == "zh":
        warning = "\n⚠️ replace 模式会删除文件中不存在的目的地与路由（含其队列记录）。" if mode == "replace" else ""
        prompt = f"请发送 YAML 备份文件（模式：{mode}）。{warning}\n/cancel 取消"
    else:
        warning = "\n⚠️ replace mode will delete targets and routes not present in the file (including their queued jobs)." if mode == "replace" else ""
        prompt = f"Please send the YAML backup file (mode: {mode}).{warning}\nSend /cancel to abort."
    await event.respond(prompt)


async def handle_import_file(event: Any, ctx: AppContext, mode: str | None = None) -> None:
    lang = ctx.lang
    document = event.message.document
    if document is None or document.size > 2 * 1024 * 1024:
        await event.respond(tr(lang, "❌ Please send a YAML file no larger than 2 MB.", "❌ 请发送不超过 2 MB 的 YAML 文件。"))
        return
    mode = mode or ctx.state.import_modes.pop(event.sender_id, "merge")
    content = await event.message.download_media(file=bytes)
    try:
        report = await import_yaml(content, ctx.repo, ctx.store, archive_root=ctx.boot.archive_root, replace=mode == "replace")
    except ValueError as exc:
        await event.respond(
            tr(
                lang,
                f"❌ Import failed (no changes were made): {esc(exc)}",
                f"❌ 导入失败，未做任何修改：{esc(exc)}",
            )
        )
        return
    await ctx.watcher.refresh()
    ctx.worker.wake()
    await event.respond(
        tr(
            lang,
            f"✅ Import complete ({mode})\n{esc(report.summary())}",
            f"✅ 导入完成（{mode}）\n{esc(report.summary())}",
        )
    )


async def on_config_button(event: Any, ctx: AppContext) -> None:
    parts = event.data.decode().split(":", 2)
    action = parts[1]
    section = "main"
    if action == "s":
        section = parts[2]
    elif action == "lang":
        new_lang = "zh" if ctx.lang == "en" else "en"
        await ctx.store.set("ui.language", new_lang)
        admin_ids = getattr(getattr(ctx, "boot", None), "admin_ids", None) or (event.sender_id,)
        await sync_bot_commands(getattr(getattr(ctx, "clients", None), "bot", None), new_lang, admin_ids)
        await event.answer("已切换为简体中文" if new_lang == "zh" else "Switched to English")
        section = "main"
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
        page = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else 0
        text, buttons = await rules_view(ctx, page=page)
        await event.edit(text, buttons=buttons)
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
