"""Formatting helpers and a tiny multi-step wizard for the Bot UI."""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from telethon import Button

from app.db.models import Target

TYPE_ICONS = {"telegram": "📨", "local": "💽", "webdav": "☁️"}
STATUS_LABELS = {
    "pending": "待处理",
    "downloading": "下载中",
    "uploading": "上传中",
    "done": "完成",
    "failed": "失败",
    "skipped": "跳过",
}
KIND_LABELS = {
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
}


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


def target_label(target: Target) -> str:
    state = "" if target.enabled else "（已停用）"
    return f"{TYPE_ICONS.get(target.type, '•')} #{target.id} {target.name}{state}"


def target_detail(target: Target, config: dict[str, Any], fallback: Target | None) -> str:
    lines = [f"<b>{esc(target_label(target))}</b>", f"类型：{target.type}"]
    if target.type == "telegram":
        lines.append(f"会话：<code>{esc(config.get('chat_id'))}</code> {esc(config.get('title') or '')}")
        if config.get("topic_id"):
            lines.append(f"Topic：{esc(config.get('topic_id'))}")
        lines.append(f"超限备用：{esc(fallback.name) if fallback else '无（仅记录）'}")
    elif target.type == "local":
        lines.append(f"目录：ARCHIVE_ROOT/{esc(config.get('path') or '')}")
    elif target.type == "webdav":
        lines.append(f"地址：<code>{esc(config.get('url'))}</code>")
        if config.get("base_path"):
            lines.append(f"子目录：{esc(config.get('base_path'))}")
        lines.append(f"用户：{esc(config.get('username') or '（匿名）')}  密码：{'已设置' if config.get('password') else '未设置'}")
        lines.append(f"校验证书：{'是' if config.get('verify_tls', True) else '否'}")
    return "\n".join(lines)


def chunk_buttons(buttons: list[Any], per_row: int = 2) -> list[list[Any]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def back_button(data: bytes, text: str = "⬅️ 返回") -> Any:
    return Button.inline(text, data=data)


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

    def has(self, user_id: int) -> bool:
        wizard = self.active.get(user_id)
        if wizard and time.monotonic() - wizard.started > self.TIMEOUT:
            self.active.pop(user_id, None)
            return False
        return wizard is not None

    def cancel(self, user_id: int) -> bool:
        return self.active.pop(user_id, None) is not None

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
        await event.respond(
            f"<b>{esc(wizard.title)}</b>（{number}/{total}）\n{step.prompt}\n\n<i>发送 /cancel 取消</i>"
        )

    async def handle(self, event: Any) -> bool:
        wizard = self.active.get(event.sender_id)
        if wizard is None:
            return False
        step = wizard.current()
        if step is None:
            self.active.pop(event.sender_id, None)
            return False
        raw = event.raw_text or ""
        if step.secret:
            try:
                await event.delete()
            except Exception:
                pass
        try:
            wizard.values[step.key] = step.parse(raw)
        except ValueError as exc:
            await event.respond(f"⚠️ {esc(exc)}\n请重新输入：")
            return True
        wizard.index += 1
        await self._prompt(event, wizard)
        return True


def optional(value: str) -> str:
    value = value.strip()
    return "" if value in {"-", "无", "none", "skip"} else value


def yes_no(value: str) -> bool:
    text = value.strip().lower()
    if text in {"y", "yes", "是", "1", "true", "开", "on"}:
        return True
    if text in {"n", "no", "否", "0", "false", "关", "off"}:
        return False
    raise ValueError("请回答 是/否（y/n）")


def target_id_list(value: str) -> list[int]:
    ids = []
    for part in value.replace("，", ",").replace(" ", ",").split(","):
        part = part.strip().lstrip("#")
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(f"无效的目的地编号：{part}")
        ids.append(int(part))
    if not ids:
        raise ValueError("至少填写一个目的地编号")
    return ids


def name_value(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 64:
        raise ValueError("名称长度需为 1–64 个字符")
    return value
