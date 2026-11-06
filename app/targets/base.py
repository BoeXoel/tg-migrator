"""Target (sink) abstraction, archive path layout and sidecar metadata."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol

from app.config import ArchiveSettings, RuntimeSettings
from app.db.models import Job, Target
from app.telegram import media_helper as mh

PhaseCallback = Callable[[str], Awaitable[None]]


@dataclass
class DeliveryContext:
    job: Job
    target: Target
    target_config: dict[str, Any]
    messages: list[Any]
    settings: RuntimeSettings
    stop_event: asyncio.Event
    on_phase: PhaseCallback
    source_entity: Any = None
    source_username: str | None = None


@dataclass
class DeliveryResult:
    status: str  # done | skipped
    dest_message_ids: list[int] | None = None
    transfer_route: str | None = None
    remote_uri: str | None = None
    reason: str = ""
    reason_code: str | None = None
    writer_identity: str | None = None
    upload_limit_bytes: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class TargetHandler(Protocol):
    async def deliver(self, ctx: DeliveryContext) -> DeliveryResult: ...


# ---------------------------------------------------------------------------
# Archive layout
# ---------------------------------------------------------------------------


class ArchivePathBuilder:
    """Directory layout for local and WebDAV archives.

    Default: ``<source>/<msg>_<file>``; ``topic_<id>`` is added only when the
    message belongs to a forum topic, and ``YYYY-MM`` only when enabled.
    """

    def __init__(self, settings: ArchiveSettings) -> None:
        self.settings = settings

    def source_folder(self, job: Job) -> str:
        chat_id = str(job.source_chat_id)
        title = mh.safe_name(job.source_title or "", limit=80)
        mode = self.settings.folder_naming
        if mode == "id" or not title:
            return chat_id
        if mode == "title":
            return title
        return f"{title}_{chat_id}"

    def directory_parts(self, job: Job) -> list[str]:
        parts = [self.source_folder(job)]
        if job.source_topic_id:
            parts.append(f"topic_{job.source_topic_id}")
        if self.settings.group_by_month:
            date = job.message_date or datetime.now(timezone.utc).replace(tzinfo=None)
            parts.append(date.strftime("%Y-%m"))
        return parts

    @staticmethod
    def file_name(message: Any) -> str:
        return mh.local_file_name(message)

    @staticmethod
    def sidecar_base(job: Job) -> str:
        return str(job.source_message_id)


def _entity_dicts(entities: Any) -> list[dict[str, Any]]:
    result = []
    for entity in mh.visible_entities(entities) or []:
        item: dict[str, Any] = {
            "type": type(entity).__name__.replace("MessageEntity", "").lower(),
            "offset": entity.offset,
            "length": entity.length,
        }
        for attr in ("url", "language", "user_id", "document_id"):
            value = getattr(entity, attr, None)
            if value is not None:
                item[attr] = value
        result.append(item)
    return result


def _markdown(text: str, entities: Any) -> str:
    try:
        from telethon.extensions import markdown

        return markdown.unparse(text, mh.visible_entities(entities) or [])
    except Exception:
        return text


def _poll_summary(message: Any) -> dict[str, Any] | None:
    poll = getattr(getattr(message, "media", None), "poll", None)
    if poll is None:
        return None
    question = getattr(poll.question, "text", poll.question)
    answers = [getattr(answer.text, "text", answer.text) for answer in poll.answers or []]
    return {"question": str(question), "answers": [str(answer) for answer in answers]}


def sidecar_needed(job: Job, messages: list[Any], settings: RuntimeSettings) -> bool:
    if settings.archive.sidecar_format == "none":
        return False
    kinds = {mh.media_kind(message) for message in messages}
    if "text" in kinds or kinds & set(mh.NATIVE_ONLY_KINDS):
        return True
    if settings.archive.sidecar_always:
        return True
    if settings.transfer.drop_caption:
        return False
    return any(mh.message_text(message) for message in messages)


def build_sidecars(
    job: Job,
    messages: list[Any],
    files: dict[int, str],
    settings: RuntimeSettings,
    *,
    source_username: str | None = None,
) -> dict[str, bytes]:
    """Return ``{file_name: content}`` for the configured sidecar format(s)."""

    if not sidecar_needed(job, messages, settings):
        return {}
    keep_text = not settings.transfer.drop_caption or all(mh.media_kind(m) == "text" for m in messages)
    link = mh.source_link(job.source_chat_id, job.source_message_id, job.source_topic_id, source_username)
    first = messages[0]
    text = mh.message_text(first) if keep_text else ""
    entities = getattr(first, "entities", None) if keep_text else None
    for message in messages:
        if keep_text and not text and mh.message_text(message):
            text, entities = mh.message_text(message), getattr(message, "entities", None)

    items = []
    for message in messages:
        item: dict[str, Any] = {
            "message_id": int(message.id),
            "type": mh.media_kind(message),
            "file": files.get(int(message.id)),
            "size": mh.file_size(message),
            "original_name": mh.original_file_name(message),
        }
        item_text = mh.message_text(message) if keep_text else ""
        if item_text and item_text != text:
            item["text"] = item_text
        poll = _poll_summary(message)
        if poll:
            item["poll"] = poll
        items.append(item)

    date = job.message_date.replace(tzinfo=timezone.utc).isoformat() if job.message_date else None
    base = ArchivePathBuilder.sidecar_base(job)
    output: dict[str, bytes] = {}
    fmt = settings.archive.sidecar_format

    if fmt in {"json", "both"}:
        payload = {
            "source": {
                "chat_id": job.source_chat_id,
                "title": job.source_title,
                "topic_id": job.source_topic_id,
                "link": link or None,
            },
            "message_id": job.source_message_id,
            "message_ids": [int(message.id) for message in messages],
            "grouped_id": job.grouped_id,
            "date": date,
            "media_type": job.media_type,
            "text": text,
            "entities": _entity_dicts(entities),
            "items": items,
            "archived_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        output[f"{base}.json"] = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

    if fmt in {"md", "both"}:
        lines = [f"# {job.source_title or job.source_chat_id} · {job.source_message_id}", ""]
        if date:
            lines.append(f"- 时间：{date}")
        if link:
            lines.append(f"- 来源：{link}")
        lines.append(f"- 类型：{job.media_type}")
        file_names = [name for name in files.values() if name]
        if file_names:
            lines.append("- 文件：" + "、".join(f"[{name}](./{name.replace(' ', '%20')})" for name in file_names))
        for item in items:
            if item.get("poll"):
                lines.append(f"- 投票：{item['poll']['question']}")
                lines.extend(f"  - {answer}" for answer in item["poll"]["answers"])
        lines.append("")
        lines.append("---")
        lines.append("")
        if text:
            lines.append(_markdown(text, entities))
        for item in items:
            if item.get("text"):
                lines.extend(["", f"**#{item['message_id']}**", "", item["text"]])
        output[f"{base}.md"] = ("\n".join(lines).rstrip() + "\n").encode("utf-8")

    return output
