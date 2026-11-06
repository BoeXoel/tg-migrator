"""Message classification and metadata helpers built on Telethon types."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from telethon.tl import types

# Kinds that can be downloaded and re-uploaded.
FILE_KINDS = ("photo", "video", "animation", "audio", "voice", "video_note", "document", "sticker")
# Kinds that only a native (server-side) copy can reproduce.
NATIVE_ONLY_KINDS = ("poll", "contact", "location", "venue", "dice", "game", "invoice", "story", "giveaway", "paid_media")

MAX_THUMBNAIL_BYTES = 200_000
MAX_THUMBNAIL_DIMENSION = 320

_EXTENSIONS = {
    "photo": ".jpg",
    "video": ".mp4",
    "animation": ".mp4",
    "audio": ".mp3",
    "voice": ".ogg",
    "video_note": ".mp4",
    "sticker": ".webp",
    "document": ".bin",
}


def media_kind(message: Any) -> str:
    media = getattr(message, "media", None)
    if media is None or isinstance(media, (types.MessageMediaEmpty, types.MessageMediaWebPage)):
        return "text" if (getattr(message, "message", None) or "") else "unsupported"
    if isinstance(media, types.MessageMediaPhoto):
        return "photo" if media.photo and not isinstance(media.photo, types.PhotoEmpty) else "unsupported"
    if isinstance(media, types.MessageMediaDocument):
        document = media.document
        if not isinstance(document, types.Document):
            return "unsupported"
        attributes = document.attributes or []
        if any(isinstance(attr, types.DocumentAttributeSticker) for attr in attributes):
            return "sticker"
        if any(isinstance(attr, types.DocumentAttributeAnimated) for attr in attributes):
            return "animation"
        for attr in attributes:
            if isinstance(attr, types.DocumentAttributeVideo):
                return "video_note" if getattr(attr, "round_message", False) else "video"
        for attr in attributes:
            if isinstance(attr, types.DocumentAttributeAudio):
                return "voice" if getattr(attr, "voice", False) else "audio"
        return "document"
    simple = {
        "MessageMediaPoll": "poll",
        "MessageMediaContact": "contact",
        "MessageMediaGeo": "location",
        "MessageMediaGeoLive": "location",
        "MessageMediaVenue": "venue",
        "MessageMediaDice": "dice",
        "MessageMediaGame": "game",
        "MessageMediaInvoice": "invoice",
        "MessageMediaStory": "story",
        "MessageMediaGiveaway": "giveaway",
        "MessageMediaGiveawayResults": "giveaway",
        "MessageMediaPaidMedia": "paid_media",
    }
    return simple.get(type(media).__name__, "unsupported")


def include_flag(kind: str) -> str:
    """Name of the IncludeSettings flag controlling a kind."""

    if kind in NATIVE_ONLY_KINDS:
        return "other"
    return kind


def is_file_kind(kind: str) -> bool:
    return kind in FILE_KINDS


def media_object(message: Any) -> Any | None:
    media = getattr(message, "media", None)
    if isinstance(media, types.MessageMediaDocument) and isinstance(media.document, types.Document):
        return media.document
    if isinstance(media, types.MessageMediaPhoto) and isinstance(media.photo, types.Photo):
        return media.photo
    return None


def _photo_size_bytes(size: Any) -> int | None:
    if isinstance(size, types.PhotoSize):
        return int(size.size)
    if isinstance(size, types.PhotoSizeProgressive):
        return int(max(size.sizes)) if size.sizes else None
    if isinstance(size, types.PhotoCachedSize):
        return len(size.bytes)
    return None


def file_size(message: Any) -> int | None:
    obj = media_object(message)
    if isinstance(obj, types.Document):
        return int(obj.size) if obj.size else None
    if isinstance(obj, types.Photo):
        sizes = [value for value in (_photo_size_bytes(size) for size in obj.sizes or []) if value]
        return max(sizes) if sizes else None
    return None


def media_unique_key(message: Any) -> str:
    obj = media_object(message)
    if isinstance(obj, types.Document):
        return f"doc:{obj.id}"
    if isinstance(obj, types.Photo):
        return f"photo:{obj.id}"
    return ""


def dedupe_key(source_chat_id: str, messages: list[Any]) -> str:
    """Stable identity of a message group, used to avoid duplicate deliveries.

    Media groups are identified by Telegram's global media IDs (a repost of
    the same file is a duplicate); everything else by its message IDs.
    """

    keys = [media_unique_key(message) for message in messages]
    if keys and all(keys):
        raw = "media:" + "|".join(keys)
    else:
        raw = f"msg:{source_chat_id}:" + ",".join(str(message.id) for message in messages)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def message_text(message: Any) -> str:
    return getattr(message, "message", None) or ""


def is_spoiler_entity(entity: Any) -> bool:
    return isinstance(entity, types.MessageEntitySpoiler)


def has_spoiler(message: Any, *, include_text: bool = True) -> bool:
    if getattr(getattr(message, "media", None), "spoiler", False):
        return True
    if include_text:
        return any(is_spoiler_entity(entity) for entity in (getattr(message, "entities", None) or []))
    return False


def visible_entities(entities: Iterable[Any] | None) -> list[Any] | None:
    result = [entity for entity in (entities or []) if not is_spoiler_entity(entity)]
    return result or None


def topic_of(message: Any) -> int | None:
    reply_to = getattr(message, "reply_to", None)
    if reply_to is None or not getattr(reply_to, "forum_topic", False):
        return None
    return getattr(reply_to, "reply_to_top_id", None) or getattr(reply_to, "reply_to_msg_id", None)


def message_in_topic(message: Any, topic_id: int) -> bool:
    reply_to = getattr(message, "reply_to", None)
    if reply_to is None:
        return False
    return topic_id in {getattr(reply_to, "reply_to_top_id", None), getattr(reply_to, "reply_to_msg_id", None)}


def message_date(message: Any) -> datetime | None:
    value = getattr(message, "date", None)
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def original_file_name(message: Any) -> str | None:
    obj = media_object(message)
    if isinstance(obj, types.Document):
        for attr in obj.attributes or []:
            if isinstance(attr, types.DocumentAttributeFilename) and attr.file_name:
                return attr.file_name
    return None


def guess_extension(message: Any) -> str:
    kind = media_kind(message)
    obj = media_object(message)
    mime = getattr(obj, "mime_type", "") or ""
    by_mime = {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
        "audio/mpeg": ".mp3",
        "audio/ogg": ".ogg",
        "audio/mp4": ".m4a",
        "audio/flac": ".flac",
        "image/webp": ".webp",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
        "application/x-tgsticker": ".tgs",
        "application/pdf": ".pdf",
        "application/zip": ".zip",
    }
    return by_mime.get(mime, _EXTENSIONS.get(kind, ".bin"))


_UNSAFE_FS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')


def safe_name(value: str, *, limit: int = 120) -> str:
    """Filesystem/WebDAV-safe name that keeps Unicode (e.g. Chinese) readable."""

    cleaned = _UNSAFE_FS.sub("_", value).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    encoded = cleaned.encode("utf-8")
    if len(encoded) > limit:
        cleaned = encoded[:limit].decode("utf-8", errors="ignore").rstrip()
    return cleaned


def local_file_name(message: Any) -> str:
    original = original_file_name(message)
    if original:
        stem = safe_name(original)
        if stem:
            return f"{message.id}_{stem}"
    return f"{message.id}{guess_extension(message)}"


def thumbnail_candidate(message: Any) -> Any | None:
    """Pick a small JPEG preview usable as an upload thumbnail."""

    obj = media_object(message)
    if not isinstance(obj, types.Document):
        return None
    candidates: list[tuple[int, int, Any]] = []
    for thumb in obj.thumbs or []:
        if not isinstance(thumb, (types.PhotoSize, types.PhotoCachedSize)):
            continue
        width, height = int(thumb.w or 0), int(thumb.h or 0)
        size = _photo_size_bytes(thumb) or 0
        if width <= 0 or height <= 0 or width > MAX_THUMBNAIL_DIMENSION or height > MAX_THUMBNAIL_DIMENSION:
            continue
        if size >= MAX_THUMBNAIL_BYTES:
            continue
        candidates.append((width * height, size, thumb))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def thumbnail_reservation(message: Any) -> int:
    thumb = thumbnail_candidate(message)
    if thumb is None:
        return 0
    return _photo_size_bytes(thumb) or MAX_THUMBNAIL_BYTES


def _construct(cls: type, **values: Any) -> Any:
    """Build a TL object with only the arguments this Telethon layer supports."""

    import inspect

    accepted = set(inspect.signature(cls.__init__).parameters)
    return cls(**{key: value for key, value in values.items() if key in accepted})


def upload_attributes(message: Any) -> list[Any]:
    """Source document attributes, sanitised for a fresh upload.

    Keeps file name, video size/duration/streaming flag, audio title/performer
    /waveform, animation and sticker markers.  Drops streaming hints that only
    describe the original encoded file (preload prefix, start timestamp).
    """

    obj = media_object(message)
    if not isinstance(obj, types.Document):
        return []
    result: list[Any] = []
    for attr in obj.attributes or []:
        if isinstance(attr, types.DocumentAttributeVideo):
            result.append(
                _construct(
                    types.DocumentAttributeVideo,
                    duration=attr.duration,
                    w=attr.w,
                    h=attr.h,
                    round_message=getattr(attr, "round_message", None),
                    supports_streaming=getattr(attr, "supports_streaming", None),
                    nosound=getattr(attr, "nosound", None),
                )
            )
        else:
            result.append(attr)
    return result


def media_manifest(messages: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "message_id": int(message.id),
            "type": media_kind(message),
            "size": file_size(message),
            "name": original_file_name(message),
        }
        for message in messages
    ]


def group_media_type(messages: list[Any]) -> str:
    kinds = {media_kind(message) for message in messages}
    return next(iter(kinds)) if len(kinds) == 1 else "album"


def group_file_size(messages: list[Any]) -> int | None:
    sizes = [file_size(message) for message in messages if is_file_kind(media_kind(message))]
    if not sizes or any(size is None for size in sizes):
        return None
    return sum(size for size in sizes if size is not None)


def group_caption(messages: list[Any]) -> str | None:
    for message in messages:
        text = message_text(message)
        if text:
            return text
    return None


def source_link(chat_id: str, message_id: int, topic_id: int | None = None, username: str | None = None) -> str:
    if username:
        base = f"https://t.me/{username.lstrip('@')}"
    elif str(chat_id).startswith("-100"):
        base = f"https://t.me/c/{str(chat_id)[4:]}"
    else:
        return ""
    return f"{base}/{topic_id}/{message_id}" if topic_id else f"{base}/{message_id}"
