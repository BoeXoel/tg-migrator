"""Formatting helpers, bilingual UI utilities, secret extraction, and multi-step Wizard."""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from telethon import Button
from telethon.tl import types

from app.db.models import Target

TYPE_ICONS = {"telegram": "📨", "local": "💽", "webdav": "☁️"}

STATUS_LABELS_I18N: dict[str, dict[str, str]] = {
    "en": {
        "pending": "Pending",
        "downloading": "Downloading",
        "uploading": "Uploading",
        "done": "Done",
        "failed": "Failed",
        "skipped": "Skipped",
    },
    "zh": {
        "pending": "待处理",
        "downloading": "下载中",
        "uploading": "上传中",
        "done": "完成",
        "failed": "失败",
        "skipped": "跳过",
    },
}

KIND_LABELS_I18N: dict[str, dict[str, str]] = {
    "en": {
        "text": "Text",
        "photo": "Photos",
        "video": "Videos",
        "animation": "GIFs",
        "audio": "Audio",
        "voice": "Voice Notes",
        "video_note": "Video Notes",
        "document": "Documents",
        "sticker": "Stickers",
        "other": "Other (Polls/Locations, native copy only)",
    },
    "zh": {
        "text": "文本",
        "photo": "图片",
        "video": "视频",
        "animation": "GIF 动图",
        "audio": "音频",
        "voice": "语音",
        "video_note": "圆形视频",
        "document": "文件",
        "sticker": "贴纸",
        "other": "其他（投票/位置等，仅原生复制）",
    },
}

# Backwards-compatible aliases
STATUS_LABELS = STATUS_LABELS_I18N["en"]
KIND_LABELS = KIND_LABELS_I18N["en"]

_DESTRUCTIVE_FORMAT_ENTITIES = (
    types.MessageEntityBold,
    types.MessageEntityItalic,
    types.MessageEntityStrike,
    types.MessageEntityUnderline,
    types.MessageEntitySpoiler,
    types.MessageEntityBlockquote,
)
_CODE_ENTITIES = (
    types.MessageEntityCode,
    types.MessageEntityPre,
)


def tr(lang: str, en: str, zh: str) -> str:
    return zh if lang == "zh" else en


def status_labels(lang: str = "en") -> dict[str, str]:
    return STATUS_LABELS_I18N.get(lang, STATUS_LABELS_I18N["en"])


def kind_labels(lang: str = "en") -> dict[str, str]:
    return KIND_LABELS_I18N.get(lang, KIND_LABELS_I18N["en"])


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def command(name: str) -> "re.Pattern[str]":
    """Pattern for ``/name``, ``/name@bot`` and ``/name args``."""

    return re.compile(rf"^/{name}(?:@\w+)?(?:\s+(.*))?$", re.DOTALL | re.IGNORECASE)


def command_args(event: Any) -> str:
    match = getattr(event, "pattern_match", None)
    if match is None or match.lastindex is None:
        return ""
    return (match.group(1) or "").strip()


def onoff(value: bool) -> str:
    return "✅" if value else "⬜"


def target_label(target: Target, lang: str = "en") -> str:
    disabled = tr(lang, " (disabled)", "（已停用）")
    state = "" if target.enabled else disabled
    return f"{TYPE_ICONS.get(target.type, '•')} #{target.id} {target.name}{state}"


def target_detail(target: Target, config: dict[str, Any], fallback: Target | None, lang: str = "en") -> str:
    lines = [
        f"<b>{esc(target_label(target, lang))}</b>",
        f"{tr(lang, 'Type', '类型')}: {target.type}",
    ]
    if target.type == "telegram":
        lines.append(f"{tr(lang, 'Chat', '会话')}: <code>{esc(config.get('chat_id'))}</code> {esc(config.get('title') or '')}")
        if config.get("topic_id"):
            lines.append(f"Topic: {esc(config.get('topic_id'))}")
        fb_name = esc(fallback.name) if fallback else tr(lang, "None (record only)", "无（仅记录）")
        lines.append(f"{tr(lang, 'Oversized fallback', '超限备用')}: {fb_name}")
    elif target.type == "local":
        lines.append(f"{tr(lang, 'Directory', '目录')}: ARCHIVE_ROOT/{esc(config.get('path') or '')}")
    elif target.type == "webdav":
        lines.append(f"{tr(lang, 'URL', '地址')}: <code>{esc(config.get('url'))}</code>")
        if config.get("base_path"):
            lines.append(f"{tr(lang, 'Subdirectory', '子目录')}: {esc(config.get('base_path'))}")
        anon = tr(lang, "(anonymous)", "（匿名）")
        pw_set = tr(lang, "Set" if config.get("password") else "Not set", "已设置" if config.get("password") else "未设置")
        lines.append(
            f"{tr(lang, 'User', '用户')}: {esc(config.get('username') or anon)}  "
            f"{tr(lang, 'Password', '密码')}: {pw_set}"
        )
        tls_val = tr(lang, "Yes" if config.get("verify_tls", True) else "No", "是" if config.get("verify_tls", True) else "否")
        lines.append(f"{tr(lang, 'Verify TLS', '校验证书')}: {tls_val}")
    return "\n".join(lines)


def chunk_buttons(buttons: list[Any], per_row: int = 2) -> list[list[Any]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def back_button(data: bytes, text: str | None = None, *, lang: str = "en") -> Any:
    if text is None:
        if data == b"m:home":
            text = tr(lang, "⬅️ Main Menu", "⬅️ 返回主菜单")
        else:
            text = tr(lang, "⬅️ Back", "⬅️ 返回")
    return Button.inline(text, data=data)


def extract_secret_text(event: Any, lang: str = "en") -> str:
    """Extract a secret/password from a Telegram message without losing Markdown symbols.

    If Telegram parsed parts of the password into bold/italic/strike/spoiler
    entities (which strips characters like ``_``, ``*``, ``~``, or ``||``),
    raise a ``ValueError`` instructing the user to resend wrapped in backticks.
    """

    raw = event.raw_text or ""
    msg = getattr(event, "message", None)
    entities = getattr(msg, "entities", None) or []

    has_code = any(isinstance(ent, _CODE_ENTITIES) for ent in entities)
    has_destructive = any(isinstance(ent, _DESTRUCTIVE_FORMAT_ENTITIES) for ent in entities)

    if has_destructive and not has_code:
        raise ValueError(
            tr(
                lang,
                "Telegram formatted symbols (_, *, ~, ||) in your secret and stripped them. "
                "Please resend wrapped in backticks, e.g. `your_password`",
                "检测到 Telegram 将密码中的特殊符号（_、*、~、||）自动转为了粗体/斜体格式导致符号丢失。"
                "请使用反引号包裹密码后重新发送，例如：`你的密码`",
            )
        )

    # If the client did not convert `secret` into a MessageEntityCode, strip one outer pair of backticks.
    if not has_code and len(raw) >= 2 and raw.startswith("`") and raw.endswith("`"):
        raw = raw[1:-1]
    return raw


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------


@dataclass
class Step:
    key: str
    prompt: str
    parse: Callable[[str], Any] = lambda value: value.strip()
    secret: bool = False
    skip_if: Callable[[dict[str, Any]], bool] | None = None


@dataclass
class Wizard:
    title: str
    steps: list[Step]
    on_complete: Callable[[Any, dict[str, Any]], Awaitable[None]]
    lang: str = "en"
    back_data: bytes = b"m:home"
    values: dict[str, Any] = field(default_factory=dict)
    index: int = 0
    started: float = field(default_factory=time.monotonic)

    def current(self) -> Step | None:
        while self.index < len(self.steps):
            step = self.steps[self.index]
            if step.skip_if and step.skip_if(self.values):
                self.index += 1
                continue
            return step
        return None


class WizardManager:
    TIMEOUT = 900

    def __init__(self) -> None:
        self.active: dict[int, Wizard] = {}

    def get(self, user_id: int) -> Wizard | None:
        wizard = self.active.get(user_id)
        if wizard and time.monotonic() - wizard.started > self.TIMEOUT:
            self.active.pop(user_id, None)
            return None
        return wizard

    def has(self, user_id: int) -> bool:
        return self.get(user_id) is not None

    def cancel(self, user_id: int) -> Wizard | None:
        return self.active.pop(user_id, None)

    async def start(self, event: Any, wizard: Wizard) -> None:
        self.active[event.sender_id] = wizard
        await self._prompt(event, wizard)

    async def _prompt(self, event: Any, wizard: Wizard) -> None:
        step = wizard.current()
        if step is None:
            self.active.pop(event.sender_id, None)
            await wizard.on_complete(event, wizard.values)
            return
        total = len([s for s in wizard.steps if not (s.skip_if and s.skip_if(wizard.values))])
        number = min(wizard.index + 1, total)
        cancel_hint = tr(wizard.lang, "Send /cancel or tap Cancel below to abort", "发送 /cancel 或点击下方按钮取消")
        cancel_btn = Button.inline(tr(wizard.lang, "✖️ Cancel", "✖️ 取消"), data=b"wiz:cancel")
        await event.respond(
            f"<b>{esc(wizard.title)}</b> ({number}/{total})\n{step.prompt}\n\n<i>{cancel_hint}</i>",
            buttons=[[cancel_btn]],
        )

    async def handle(self, event: Any) -> bool:
        wizard = self.get(event.sender_id)
        if wizard is None:
            return False
        step = wizard.current()
        if step is None:
            self.active.pop(event.sender_id, None)
            return False
        if step.secret:
            try:
                await event.delete()
            except Exception:
                pass
        try:
            raw = extract_secret_text(event, wizard.lang) if step.secret else (event.raw_text or "")
            wizard.values[step.key] = step.parse(raw)
        except ValueError as exc:
            retry_label = tr(wizard.lang, "Please try again:", "请重新输入：")
            cancel_btn = Button.inline(tr(wizard.lang, "✖️ Cancel", "✖️ 取消"), data=b"wiz:cancel")
            await event.respond(f"⚠️ {esc(exc)}\n{retry_label}", buttons=[[cancel_btn]])
            return True
        wizard.index += 1
        await self._prompt(event, wizard)
        return True


def optional(value: str) -> str:
    value = value.strip()
    return "" if value.lower() in {"-", "无", "none", "skip"} else value


def yes_no(value: str) -> bool:
    text = value.strip().lower()
    if text in {"y", "yes", "是", "1", "true", "开", "on"}:
        return True
    if text in {"n", "no", "否", "0", "false", "关", "off"}:
        return False
    raise ValueError("Please reply y/n (是/否)")


def target_id_list(value: str) -> list[int]:
    ids = []
    for part in value.replace("，", ",").replace(" ", ",").split(","):
        part = part.strip().lstrip("#")
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(f"Invalid target ID: {part}")
        ids.append(int(part))
    if not ids:
        raise ValueError("Please specify at least one target ID")
    return ids


def name_value(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 64:
        raise ValueError("Name must be 1–64 characters")
    return value
