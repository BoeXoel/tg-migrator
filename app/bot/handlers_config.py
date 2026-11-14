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
    Step,
    Wizard,
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
from app.engine.reaction_tagger import normalize_emoji, normalize_tag, parse_emoji_tag_pair

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
            [
                Button.inline(tr(lang, "➕ Add Keyword", "➕ 添加关键词"), b"cfg:fadd:keyword"),
                Button.inline(tr(lang, "➕ Add Regex", "➕ 添加正则"), b"cfg:fadd:regex"),
            ],
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
            Button.inline(tr(lang, "🏷 Emoji Tags", "🏷 表情标签"), b"cfg:s:tags"),
            Button.inline(tr(lang, "🛠 Advanced", "🛠 高级参数"), b"cfg:s:advanced"),
        ],
        [
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
    add_buttons = [
        Button.inline(tr(lang, "➕ Add Keyword", "➕ 添加关键词"), b"cfg:fadd:keyword"),
        Button.inline(tr(lang, "➕ Add Regex", "➕ 添加正则"), b"cfg:fadd:regex"),
    ]
    if not rules:
        return (
            tr(
                lang,
                "No filter rules configured.\n"
                "<code>/filter_add keyword &lt;word&gt;</code> · <code>/filter_add regex &lt;pattern&gt;</code>",
                "暂无过滤规则。\n"
                "<code>/filter_add keyword 关键词</code> · <code>/filter_add regex 正则</code>",
            ),
            [add_buttons, [back_button(b"cfg:s:filters", lang=lang)]],
        )
    page_items, page, total_pages = paginate_items(rules, page, PAGE_SIZE)
    page_suffix = f" ({page + 1}/{total_pages})" if total_pages > 1 else ""
    lines = [tr(lang, f"<b>🧹 Filter Rules{page_suffix}</b>", f"<b>🧹 过滤规则{page_suffix}</b>")]
    for rule in page_items:
        lines.append(f"• <code>#{rule.id}</code> {'✅' if rule.enabled else '⬜'} {rule.rule_type}: <code>{esc(rule.pattern)}</code>")
    lines.append(
        tr(
            lang,
            "\n<code>/filter_add keyword &lt;word&gt;</code> · <code>/filter_add regex &lt;pattern&gt;</code>\n"
            "<code>/filter_del &lt;ID&gt;</code> to delete · <code>/filter_toggle &lt;ID&gt;</code> to enable/disable",
            "\n<code>/filter_add keyword 关键词</code> · <code>/filter_add regex 正则</code>\n"
            "<code>/filter_del ID</code> 删除 · <code>/filter_toggle ID</code> 启停",
        )
    )
    rows: list[list[Any]] = [add_buttons]
    nav = pagination_row("cfg:rules", page, total_pages, lang=lang)
    if nav:
        rows.append(nav)
    rows.append([back_button(b"cfg:s:filters", lang=lang)])
    return "\n".join(lines), rows


def _filter_wizard(ctx: AppContext, rule_type: str) -> Wizard:
    lang = ctx.lang
    rule_type = "regex" if rule_type == "regex" else "keyword"

    def parse_pattern(value: str) -> str:
        cleaned = value.strip()
        prefix = f"/filter_add {rule_type}"
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
        validate_rule(rule_type, cleaned)
        return cleaned

    async def done(evt: Any, values: dict[str, Any]) -> None:
        rule = await ctx.repo.add_filter(rule_type, values["pattern"])
        note = (
            ""
            if ctx.store.current.filters.enabled
            else tr(
                lang,
                "\n⚠️ Content filtering is currently disabled: <code>/set filters.enabled true</code>",
                "\n⚠️ 过滤当前未启用：<code>/set filters.enabled true</code>",
            )
        )
        text, buttons = await rules_view(ctx)
        header = tr(lang, f"✅ Added rule #{rule.id}{note}\n\n", f"✅ 已添加规则 #{rule.id}{note}\n\n")
        await evt.respond(header + text, buttons=buttons)

    if rule_type == "regex":
        title = tr(lang, "Add Regex Filter Rule", "添加正则过滤规则")
        prompt = tr(
            lang,
            "Send the regular expression pattern to block (standard Python regex syntax):\n\n"
            "📌 <b>Common Templates (tap to copy):</b>\n"
            "• Multi-word match (any word triggers skip):\n"
            "  <code>(promo|sponsored|giveaway|affiliate)</code>\n"
            "• Block external links &amp; Telegram invites:\n"
            "  <code>https?://|t\\.me/(\\+|joinchat/)</code>\n\n"
            "💡 Tip: You can also add directly anytime via command:\n"
            "<code>/filter_add regex &lt;pattern&gt;</code>",
            "请直接发送要拦截的正则表达式（支持标准正则语法）：\n\n"
            "📌 <b>常用正则模板（点击即可复制）：</b>\n"
            "• 多词合并匹配（命中任一即跳过）：\n"
            "  <code>(广告|推广|抽奖|合作)</code>\n"
            "• 拦截外部网页链接与群聊邀请链接：\n"
            "  <code>https?://|t\\.me/(\\+|joinchat/)</code>\n\n"
            "💡 提示：平时也可直接使用命令添加：\n"
            "<code>/filter_add regex 正则表达式</code>",
        )
    else:
        title = tr(lang, "Add Keyword Filter Rule", "添加关键词过滤规则")
        prompt = tr(
            lang,
            "Send the keyword or phrase you want to block (any message or Album whose text/caption contains this substring will be skipped):\n\n"
            "💡 Tip: You can also add directly anytime via command:\n"
            "<code>/filter_add keyword &lt;word&gt;</code>",
            "请直接发送要拦截的关键词或短语（正文或配文中包含该词的消息及整组相册将被跳过）：\n\n"
            "💡 提示：平时也可直接使用命令添加：\n"
            "<code>/filter_add keyword 你的关键词</code>",
        )

    return Wizard(
        title,
        [Step("pattern", prompt, parse_pattern)],
        done,
        lang=lang,
        back_data=b"cfg:rules",
    )


async def rules_text(ctx: AppContext, page: int = 0) -> str:
    text, _ = await rules_view(ctx, page=page)
    return text


async def tags_view(ctx: AppContext, page: int = 0, *, back_data: bytes = b"cfg:main") -> tuple[str, list[list[Any]]]:
    lang = ctx.lang
    rules = await ctx.repo.list_reaction_tags()
    toggle_btn = _toggle_button(
        ctx,
        "tags.enabled",
        tr(lang, "Enable emoji reaction auto-tagging", "启用表情回应自动打标"),
    )
    add_btn = Button.inline(tr(lang, "➕ Add / Edit Emoji Tag", "➕ 添加/修改表情标签"), b"cfg:tadd")

    header_desc = tr(
        lang,
        "<b>🏷 Emoji Reaction Auto-Tags</b>\n"
        "When an admin reacts to a Bot-delivered message in a target group/topic, the mapped <code>#TAG</code> is synced at the start of the message (single-space separated, no newline).",
        "<b>🏷 表情回应自动打标</b>\n"
        "管理员在目的地群组/子话题对 Bot 发送的归档消息点亮或取消 Emoji 时，自动在消息开头同步对应 <code>#TAG</code>（单空格分隔、不换行）。",
    )

    if not rules:
        body = tr(
            lang,
            "\n\nNo emoji tag mappings configured.\n"
            "<code>/tag_set ❤️ #tgm_fav</code> to add or update",
            "\n\n暂无表情标签映射。\n"
            "<code>/tag_set ❤️ #tgm_fav</code> 添加或修改",
        )
        return header_desc + body, [[toggle_btn], [add_btn], [back_button(back_data, lang=lang)]]

    page_items, page, total_pages = paginate_items(rules, page, PAGE_SIZE)
    page_suffix = f" ({page + 1}/{total_pages})" if total_pages > 1 else ""
    lines = [header_desc, "", tr(lang, f"<b>Mappings{page_suffix}</b>", f"<b>当前映射{page_suffix}</b>")]
    for rule in page_items:
        lines.append(
            f"• <code>#{rule.id}</code> {'✅' if rule.enabled else '⬜'} {esc(rule.emoji)} → <code>{esc(rule.tag)}</code>"
        )
    lines.append(
        tr(
            lang,
            "\n<code>/tag_set &lt;emoji&gt; &lt;#tag&gt;</code> to add/update\n"
            "<code>/tag_del &lt;ID&gt;</code> to delete · <code>/tag_toggle &lt;ID&gt;</code> to enable/disable",
            "\n<code>/tag_set 表情 #标签</code> 添加或修改\n"
            "<code>/tag_del ID</code> 删除 · <code>/tag_toggle ID</code> 启停",
        )
    )
    rows: list[list[Any]] = [[toggle_btn], [add_btn]]
    nav = pagination_row("cfg:tags", page, total_pages, lang=lang)
    if nav:
        rows.append(nav)
    rows.append([back_button(back_data, lang=lang)])
    return "\n".join(lines), rows


def _tag_wizard(ctx: AppContext) -> Wizard:
    lang = ctx.lang

    def parse_first(value: str) -> dict[str, str]:
        cleaned = value.strip()
        for prefix in ("/tag_set", "/tag_add"):
            if cleaned.lower().startswith(prefix):
                cleaned = cleaned[len(prefix) :].strip()
                break
        parts = cleaned.split(None, 1)
        if not parts:
            raise ValueError(
                tr(lang, "Please send <emoji> <#tag> (e.g. 🔥 #tgm_hot)", "请发送「表情 #标签」（例如：🔥 #tgm_hot）")
            )
        emoji, emoji_key = normalize_emoji(parts[0])
        result: dict[str, str] = {"emoji": emoji, "emoji_key": emoji_key}
        if len(parts) > 1 and parts[1].strip():
            result["tag"] = normalize_tag(parts[1])
        return result

    def parse_second_tag(value: str) -> str:
        return normalize_tag(value)

    async def done(evt: Any, values: dict[str, Any]) -> None:
        entry = dict(values["entry"])
        tag = entry.get("tag") or values["tag"]
        if "tag" in values["entry"]:
            entities = getattr(getattr(evt, "message", None), "entities", None)
            if entities:
                try:
                    disp, key, _ = parse_emoji_tag_pair(evt.raw_text or "", entities=entities)
                    entry["emoji"], entry["emoji_key"] = disp, key
                except ValueError:
                    pass
        rule, created = await ctx.repo.upsert_reaction_tag(entry["emoji"], entry["emoji_key"], tag)
        note = (
            ""
            if ctx.store.current.tags.enabled
            else tr(
                lang,
                "\n⚠️ Emoji auto-tagging is currently disabled: <code>/set tags.enabled true</code>",
                "\n⚠️ 表情自动打标当前未启用：<code>/set tags.enabled true</code>",
            )
        )
        view_text, buttons = await tags_view(ctx)
        action_zh = "已添加映射" if created else "已更新映射"
        action_en = "Added mapping" if created else "Updated mapping"
        header = tr(
            lang,
            f"✅ {action_en} #{rule.id}: {esc(rule.emoji)} → <code>{esc(rule.tag)}</code>{note}\n\n",
            f"✅ {action_zh} #{rule.id}：{esc(rule.emoji)} → <code>{esc(rule.tag)}</code>{note}\n\n",
        )
        await evt.respond(header + view_text, buttons=buttons)

    title = tr(lang, "Add / Update Emoji Tag", "添加/修改表情标签映射")
    prompt_step1 = tr(
        lang,
        "Send the <b>emoji</b> and <b>#tag</b> separated by a space (if the emoji already exists, its tag will be updated):\n\n"
        "📌 <b>Examples (tap to copy):</b>\n"
        "• <code>🔥 #tgm_hot</code>\n"
        "• <code>⭐ #tgm_star</code>\n"
        "• <code>❤️ #my_fav</code> (updates the default ❤️ tag)\n\n"
        "💡 Note: Use underscores <code>_</code> instead of hyphens <code>-</code> so the whole tag stays clickable in Telegram.\n"
        "Command shortcut: <code>/tag_set &lt;emoji&gt; &lt;#tag&gt;</code>",
        "请发送 <b>Emoji 表情</b> 与对应的 <b>#标签</b>（中间加空格；若该表情已存在则会直接修改其标签）：\n\n"
        "📌 <b>示例（点击即可复制）：</b>\n"
        "• <code>🔥 #tgm_hot</code>\n"
        "• <code>⭐ #tgm_star</code>\n"
        "• <code>❤️ #my_fav</code>（直接修改默认红心标签）\n\n"
        "💡 提示：标签建议使用下划线 <code>_</code> 而非连字符 <code>-</code>，且尽量带有专属前缀以免与原消息标签冲突（未带 <code>#</code> 会自动补全）。\n"
        "平时也可直接使用命令：<code>/tag_set 表情 #标签</code>",
    )
    prompt_step2 = tr(
        lang,
        "Now send the <b>#tag</b> for this emoji (for example: <code>#tgm_hot</code>):",
        "请继续发送该表情对应的 <b>#标签</b>（例如：<code>#tgm_hot</code>）：",
    )

    return Wizard(
        title,
        [
            Step("entry", prompt_step1, parse_first),
            Step("tag", prompt_step2, parse_second_tag, skip_if=lambda v: "tag" in v.get("entry", {})),
        ],
        done,
        lang=lang,
        back_data=b"cfg:s:tags",
    )


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


async def cmd_tags(event: Any, ctx: AppContext) -> None:
    arg = command_args(event).strip()
    page = max(0, int(arg) - 1) if arg.isdigit() else 0
    text, buttons = await tags_view(ctx, page=page, back_data=b"m:home")
    await event.respond(text, buttons=buttons)


async def cmd_tag_set(event: Any, ctx: AppContext) -> None:
    lang = ctx.lang
    raw = command_args(event)
    if not raw:
        await event.respond(
            tr(
                lang,
                "Usage: <code>/tag_set &lt;emoji&gt; &lt;#tag&gt;</code>\nExample: <code>/tag_set 🔥 #tgm_hot</code>",
                "用法：<code>/tag_set 表情 #标签</code>\n示例：<code>/tag_set 🔥 #tgm_hot</code>",
            )
        )
        return
    entities = getattr(getattr(event, "message", None), "entities", None)
    try:
        emoji, emoji_key, tag = parse_emoji_tag_pair(event.raw_text or raw, entities=entities)
    except ValueError as exc:
        await event.respond(f"❌ {esc(exc)}")
        return
    rule, created = await ctx.repo.upsert_reaction_tag(emoji, emoji_key, tag)
    note = (
        ""
        if ctx.store.current.tags.enabled
        else tr(
            lang,
            "\n⚠️ Emoji auto-tagging is currently disabled: <code>/set tags.enabled true</code>",
            "\n⚠️ 表情自动打标当前未启用：<code>/set tags.enabled true</code>",
        )
    )
    await event.respond(
        tr(
            lang,
            f"✅ {'Added' if created else 'Updated'} mapping #{rule.id}: {esc(rule.emoji)} → <code>{esc(rule.tag)}</code>{note}",
            f"✅ {'已添加' if created else '已更新'}映射 #{rule.id}：{esc(rule.emoji)} → <code>{esc(rule.tag)}</code>{note}",
        )
    )


async def _find_tag_rule(ctx: AppContext, raw: str) -> Any:
    token = raw.strip().lstrip("#") if raw.strip().lstrip("#").isdigit() else raw.strip()
    rules = await ctx.repo.list_reaction_tags()
    if token.isdigit():
        rid = int(token)
        return next((r for r in rules if r.id == rid), None)
    try:
        _, key = normalize_emoji(raw.strip())
    except ValueError:
        return None
    return next((r for r in rules if r.emoji_key == key), None)


async def cmd_tag_del(event: Any, ctx: AppContext) -> None:
    raw = command_args(event).strip()
    if not raw:
        await event.respond(ctx.t("Usage: /tag_del <id or emoji>", "用法：/tag_del ID或表情"))
        return
    rule = await _find_tag_rule(ctx, raw)
    if rule is None:
        await event.respond(ctx.t("Mapping does not exist.", "映射不存在。"))
        return
    await ctx.repo.delete_reaction_tag(rule.id)
    await event.respond(
        ctx.t(
            f"🗑 Deleted mapping #{rule.id} ({esc(rule.emoji)} → <code>{esc(rule.tag)}</code>).",
            f"🗑 已删除映射 #{rule.id}（{esc(rule.emoji)} → <code>{esc(rule.tag)}</code>）。",
        )
    )


async def cmd_tag_toggle(event: Any, ctx: AppContext) -> None:
    raw = command_args(event).strip()
    if not raw:
        await event.respond(ctx.t("Usage: /tag_toggle <id or emoji>", "用法：/tag_toggle ID或表情"))
        return
    rule = await _find_tag_rule(ctx, raw)
    if rule is None:
        await event.respond(ctx.t("Mapping does not exist.", "映射不存在。"))
        return
    await ctx.repo.set_reaction_tag_enabled(rule.id, not rule.enabled)
    state_en = "disabled" if rule.enabled else "enabled"
    state_zh = "停用" if rule.enabled else "启用"
    await event.respond(
        ctx.t(
            f"Mapping #{rule.id} ({esc(rule.emoji)}) {state_en}.",
            f"映射 #{rule.id}（{esc(rule.emoji)}）已{state_zh}。",
        )
    )


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
    elif action == "fadd":
        rule_type = parts[2] if len(parts) > 2 else "keyword"
        await event.answer()
        await ctx.state.wizards.start(event, _filter_wizard(ctx, rule_type))
        return
    elif action == "tags":
        page = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else 0
        text, buttons = await tags_view(ctx, page=page)
        await event.edit(text, buttons=buttons)
        return
    elif action == "tadd":
        await event.answer()
        await ctx.state.wizards.start(event, _tag_wizard(ctx))
        return
    elif action == "export":
        await event.answer()
        await _send_export(event, ctx, False)
        return
    if section == "tags":
        text, buttons = await tags_view(ctx)
        await event.edit(text, buttons=buttons)
        return
    text, buttons = config_view(ctx, section)
    await event.edit(text, buttons=buttons)


def _section_for(path: str) -> str:
    if path.startswith("transfer.include."):
        return "media"
    return path.split(".", 1)[0] if path.split(".", 1)[0] in {"transfer", "archive", "filters", "tags"} else "main"


def register(bot: TelegramClient, ctx: AppContext) -> None:
    commands = {
        "config": cmd_config,
        "set": cmd_set,
        "get": cmd_get,
        "filters": cmd_filters,
        "filter_add": cmd_filter_add,
        "filter_del": cmd_filter_del,
        "filter_toggle": cmd_filter_toggle,
        "tags": cmd_tags,
        "tag_set": cmd_tag_set,
        "tag_add": cmd_tag_set,
        "tag_del": cmd_tag_del,
        "tag_toggle": cmd_tag_toggle,
        "export": cmd_export,
        "import": cmd_import,
    }
    for name, handler in commands.items():
        bot.add_event_handler(admin_only(ctx, handler), events.NewMessage(pattern=command(name)))
    bot.add_event_handler(admin_only(ctx, on_config_button), events.CallbackQuery(pattern=rb"^cfg:"))
