"""Builders for Telethon TL objects that tolerate layer differences."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any

from telethon.tl import types

DATE = datetime(2026, 11, 6, 8, 0, tzinfo=timezone.utc)


def make(cls: type, **values: Any) -> Any:
    params = inspect.signature(cls.__init__).parameters
    kwargs = {}
    for name, param in params.items():
        if name == "self" or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if name in values:
            kwargs[name] = values[name]
        elif param.default is inspect.Parameter.empty:
            kwargs[name] = None
    return cls(**kwargs)


def document(
    *,
    doc_id: int = 1000,
    size: int = 1024,
    mime: str = "application/octet-stream",
    attributes: list[Any] | None = None,
    thumbs: list[Any] | None = None,
) -> Any:
    return make(
        types.Document,
        id=doc_id,
        access_hash=1,
        file_reference=b"",
        date=DATE,
        mime_type=mime,
        size=size,
        dc_id=1,
        attributes=attributes or [],
        thumbs=thumbs,
    )


def video_attr(**values: Any) -> Any:
    defaults = dict(duration=12, w=1280, h=720, supports_streaming=True)
    defaults.update(values)
    return make(types.DocumentAttributeVideo, **defaults)


def filename_attr(name: str) -> Any:
    return make(types.DocumentAttributeFilename, file_name=name)


def photo(*, photo_id: int = 2000, size: int = 50_000) -> Any:
    sizes = [
        make(types.PhotoSize, type="m", w=320, h=240, size=size // 4),
        make(types.PhotoSize, type="y", w=1280, h=960, size=size),
    ]
    return make(types.Photo, id=photo_id, access_hash=1, file_reference=b"", date=DATE, sizes=sizes, dc_id=1)


def message(
    msg_id: int = 10,
    *,
    text: str = "",
    media: Any = None,
    entities: list[Any] | None = None,
    grouped_id: int | None = None,
    topic_id: int | None = None,
    chat_id: int = 1234567890,
) -> Any:
    reply_to = None
    if topic_id:
        reply_to = make(types.MessageReplyHeader, reply_to_msg_id=topic_id, forum_topic=True)
    return make(
        types.Message,
        id=msg_id,
        peer_id=types.PeerChannel(chat_id),
        date=DATE,
        message=text,
        media=media,
        entities=entities,
        grouped_id=grouped_id,
        reply_to=reply_to,
    )


def doc_message(msg_id: int = 10, *, spoiler: bool = False, **kwargs: Any) -> Any:
    text = kwargs.pop("text", "")
    grouped_id = kwargs.pop("grouped_id", None)
    topic_id = kwargs.pop("topic_id", None)
    entities = kwargs.pop("entities", None)
    media = make(types.MessageMediaDocument, document=document(**kwargs), spoiler=spoiler or None)
    return message(msg_id, text=text, media=media, grouped_id=grouped_id, topic_id=topic_id, entities=entities)


def photo_message(msg_id: int = 10, *, spoiler: bool = False, text: str = "", grouped_id: int | None = None, photo_id: int = 2000) -> Any:
    media = make(types.MessageMediaPhoto, photo=photo(photo_id=photo_id), spoiler=spoiler or None)
    return message(msg_id, text=text, media=media, grouped_id=grouped_id)
