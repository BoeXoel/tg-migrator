"""Deliver messages to a Telegram chat or forum topic.

Order of preference:
1. Native server-side forward/copy (no download) when the reading account is
   also the writer and no spoiler must be removed.
2. Download and re-upload with the source metadata (video size/duration,
   audio tags, voice waveform, file names, safe JPEG thumbnails).
3. Oversized media: an explicitly enabled Premium reader, otherwise the
   target's fallback archive (local/WebDAV), otherwise an ``oversized`` record.
"""

from __future__ import annotations

import copy
import errno
import inspect
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from telethon import TelegramClient, helpers, utils
from telethon.tl import functions, types

from app.config import BootstrapConfig, TransferSettings
from app.engine.storage_guard import StorageGuard
from app.errors import (
    DiskFullError,
    PermanentJobError,
    RetryableJobError,
    TargetConfigError,
    TopicDeliveryError,
)
from app.targets.base import DeliveryContext, DeliveryResult
from app.telegram import media_helper as mh
from app.telegram.client_manager import AccountCapabilities, ClientManager, is_private_invite_link
from app.telegram.errors_map import BAD_REQUEST_ERRORS, CAPTION_TOO_LONG_ERRORS, FORWARD_RESTRICTED_ERRORS
from app.telegram.limiter import TelegramLimiter

log = logging.getLogger("migrator.target.telegram")

FallbackCallback = Callable[[DeliveryContext], Awaitable["DeliveryResult | None"]]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def truncate_utf16(text: str, entities: list[Any] | None, limit: int) -> tuple[str, list[Any] | None]:
    if utf16_len(text) <= limit:
        return text, entities
    encoded = text.encode("utf-16-le")[: limit * 2]
    cut = encoded.decode("utf-16-le", errors="ignore")
    cut_len = utf16_len(cut)
    kept = []
    for entity in entities or []:
        if entity.offset >= cut_len:
            continue
        if entity.offset + entity.length > cut_len:
            entity = copy.copy(entity)
            entity.length = cut_len - entity.offset
        kept.append(entity)
    return cut, kept or None


def build_request(cls: type, **values: Any) -> Any:
    """Instantiate a TL request using only parameters supported by this layer."""

    accepted = set(inspect.signature(cls.__init__).parameters)
    return cls(**{key: value for key, value in values.items() if key in accepted and value is not None})


def reply_to_topic(topic_id: int | None) -> Any:
    if not topic_id:
        return None
    return types.InputReplyToMessage(reply_to_msg_id=topic_id)


def sent_messages(result: Any) -> list[Any]:
    """Messages created by a raw request (forward / multi-media)."""

    if isinstance(result, list):
        return [item for item in result if getattr(item, "id", None)]
    if hasattr(result, "id") and hasattr(result, "peer_id"):
        return [result]
    found = []
    for update in getattr(result, "updates", None) or []:
        if isinstance(update, (types.UpdateNewMessage, types.UpdateNewChannelMessage)):
            found.append(update.message)
    return sorted(found, key=lambda message: message.id)


def _valid_thumbnail(path: Path) -> bool:
    try:
        if not path.is_file() or not 0 < path.stat().st_size < mh.MAX_THUMBNAIL_BYTES:
            return False
        with path.open("rb") as handle:
            return handle.read(3) == b"\xff\xd8\xff"
    except OSError:
        return False


class TelegramTarget:
    def __init__(
        self,
        boot: BootstrapConfig,
        clients: ClientManager,
        limiter: TelegramLimiter,
        fallback: FallbackCallback,
    ) -> None:
        self.boot = boot
        self.clients = clients
        self.limiter = limiter
        self.fallback = fallback

    # ----------------------------------------------------------------- entry

    async def deliver(self, ctx: DeliveryContext) -> DeliveryResult:
        chat = ctx.target_config.get("chat_id")
        if not chat:
            raise TargetConfigError("Telegram target has no chat_id")
        topic = int(ctx.target_config["topic_id"]) if ctx.target_config.get("topic_id") else None
        transfer = ctx.settings.transfer
        writer = self.clients.writer
        caps = self.clients.writer_capabilities(transfer.max_upload_bytes)
        if caps.is_bot and is_private_invite_link(chat):
            raise TargetConfigError("A Bot cannot post to a private invite link; bind the chat with /bind instead")

        strip = transfer.strip_spoilers and any(self._has_visible_spoiler(m, transfer) for m in ctx.messages)

        if transfer.native_copy and self.clients.writer_is_reader and not strip:
            try:
                await ctx.on_phase("uploading")
                return await self._native(ctx, writer, chat, topic, caps)
            except FORWARD_RESTRICTED_ERRORS:
                if transfer.native_only:
                    return DeliveryResult("skipped", reason="Source forbids forwarding", reason_code="forward_restricted")
                log.info("Job %s: source forbids forwarding, re-uploading instead", ctx.job.id)
            except BAD_REQUEST_ERRORS as exc:
                if not isinstance(exc, tuple(_permanent_native_errors())):
                    if transfer.native_only:
                        raise PermanentJobError(f"Native copy failed: {exc}", reason_code="telegram_bad_request") from exc
                    log.warning("Job %s: native copy failed (%s), re-uploading instead", ctx.job.id, exc)
                else:
                    raise

        if transfer.native_only:
            if strip:
                return DeliveryResult(
                    "skipped",
                    reason="native_only is on, but removing a spoiler requires a re-upload",
                    reason_code="spoiler_removal_requires_reupload",
                )
            return DeliveryResult(
                "skipped", reason="native_only is on and a native copy is unavailable", reason_code="native_unavailable"
            )

        reuploadable = [m for m in ctx.messages if mh.media_kind(m) == "text" or mh.is_file_kind(mh.media_kind(m))]
        if not reuploadable:
            return DeliveryResult(
                "skipped",
                reason="This message kind (poll, contact, location…) can only be copied natively by the reading account",
                reason_code="native_only_kind",
            )
        if all(mh.media_kind(m) == "text" for m in reuploadable):
            await ctx.on_phase("uploading")
            return await self._send_text(ctx, writer, caps, chat, topic, reuploadable[0], strip)
        return await self._reupload(ctx, reuploadable, chat, topic, strip)

    # --------------------------------------------------------------- native

    async def _native(
        self, ctx: DeliveryContext, writer: TelegramClient, chat: str, topic: int | None, caps: AccountCapabilities
    ) -> DeliveryResult:
        transfer = ctx.settings.transfer
        dest = await self.clients.resolve(writer, chat)
        request = build_request(
            functions.messages.ForwardMessagesRequest,
            from_peer=await writer.get_input_entity(ctx.source_entity),
            id=[int(m.id) for m in ctx.messages],
            random_id=[helpers.generate_random_long() for _ in ctx.messages],
            to_peer=await writer.get_input_entity(dest),
            drop_author=True if transfer.hide_sender else None,
            drop_media_captions=True if transfer.drop_caption else None,
            top_msg_id=topic,
        )
        if topic and "top_msg_id" not in inspect.signature(functions.messages.ForwardMessagesRequest.__init__).parameters:
            request = build_request(
                functions.messages.ForwardMessagesRequest,
                **{k: getattr(request, k, None) for k in ("from_peer", "id", "random_id", "to_peer", "drop_author", "drop_media_captions")},
                reply_to=reply_to_topic(topic),
            )
        result = await self.limiter.call("copy", writer, request)
        messages = sent_messages(result)
        if not messages:
            raise RetryableJobError("Telegram returned no forwarded messages", reason_code="telegram_empty_result")
        await self._check_topic(writer, dest, topic, messages)
        return DeliveryResult(
            "done",
            dest_message_ids=[int(m.id) for m in messages],
            transfer_route="tg_copy" if transfer.hide_sender else "tg_forward",
            writer_identity=caps.identity,
            upload_limit_bytes=caps.max_upload_bytes,
        )

    # ----------------------------------------------------------------- text

    async def _send_text(
        self,
        ctx: DeliveryContext,
        writer: TelegramClient,
        caps: AccountCapabilities,
        chat: str,
        topic: int | None,
        message: Any,
        strip: bool,
    ) -> DeliveryResult:
        text = mh.message_text(message)
        if not text:
            return DeliveryResult("skipped", reason="Text message is empty", reason_code="empty")
        entities = mh.visible_entities(message.entities) if strip else (message.entities or None)
        dest = await self.clients.resolve(writer, chat)
        result = await self.limiter.call(
            "upload",
            writer.send_message,
            dest,
            text,
            formatting_entities=entities,
            parse_mode=None,
            link_preview=isinstance(getattr(message, "media", None), types.MessageMediaWebPage),
            reply_to=topic,
        )
        messages = sent_messages(result)
        await self._check_topic(writer, dest, topic, messages)
        return DeliveryResult(
            "done",
            dest_message_ids=[int(m.id) for m in messages],
            transfer_route="tg_text",
            writer_identity=caps.identity,
            upload_limit_bytes=caps.max_upload_bytes,
        )

    # ------------------------------------------------------------- reupload

    async def _reupload(
        self, ctx: DeliveryContext, messages: list[Any], chat: str, topic: int | None, strip: bool
    ) -> DeliveryResult:
        transfer = ctx.settings.transfer
        writer = self.clients.writer
        caps = self.clients.writer_capabilities(transfer.max_upload_bytes)
        file_messages = [m for m in messages if mh.is_file_kind(mh.media_kind(m))]
        sizes = {int(m.id): mh.file_size(m) for m in file_messages}

        unknown = [mid for mid, size in sizes.items() if size is None]
        if unknown and not transfer.allow_download_unknown_size:
            return DeliveryResult(
                "skipped",
                reason=f"Size of message {unknown[0]} is unknown; allow_download_unknown_size is off",
                reason_code="unknown_size",
            )

        largest = max((size or 0 for size in sizes.values()), default=0)
        if largest > caps.max_upload_bytes:
            alternative = await self._premium_alternative(transfer, caps, chat, largest)
            if alternative is None:
                fallback = await self.fallback(ctx)
                if fallback is not None:
                    return fallback
                return DeliveryResult(
                    "skipped",
                    reason=(
                        f"File of {largest} bytes exceeds the {caps.max_upload_bytes}-byte limit of {caps.identity}; "
                        "set a fallback archive target to keep it"
                    ),
                    reason_code="oversized",
                    transfer_route="record",
                    writer_identity=caps.identity,
                    upload_limit_bytes=caps.max_upload_bytes,
                )
            writer, caps = alternative

        guard = StorageGuard(self.boot.active_dir, self.boot.failed_dir, ctx.settings.downloads)
        reservation = sum(size or 0 for size in sizes.values()) + sum(mh.thumbnail_reservation(m) for m in file_messages)
        guard.ensure_reservation(reservation)
        dest = await self.clients.resolve(writer, chat)

        job_dir = guard.job_dir(ctx.job.id)
        success = False
        force_delete = False
        try:
            await ctx.on_phase("downloading")
            downloaded: list[tuple[Any, Path, Path | None]] = []
            done_bytes = 0
            for message in messages:
                if mh.media_kind(message) == "text":
                    continue
                if ctx.stop_event.is_set():
                    raise RetryableJobError("Stop requested; job left for retry")
                path = await self._download(message, job_dir, guard, reservation - done_bytes)
                done_bytes += path.stat().st_size
                thumb = await self._download_thumb(message, job_dir)
                downloaded.append((message, path, thumb))

            await ctx.on_phase("uploading")
            if len(downloaded) == 1:
                ids = await self._send_single(ctx, writer, caps, dest, topic, downloaded[0], strip)
            else:
                ids = await self._send_album(ctx, writer, caps, dest, topic, downloaded, strip)
            success = True
            return DeliveryResult(
                "done",
                dest_message_ids=ids,
                transfer_route="tg_upload",
                writer_identity=caps.identity,
                upload_limit_bytes=caps.max_upload_bytes,
            )
        except DiskFullError:
            force_delete = True
            raise
        except OSError as exc:
            if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}:
                force_delete = True
                raise DiskFullError("The download filesystem is out of space") from exc
            raise
        finally:
            guard.cleanup_job_dir(job_dir, success=success, force_delete=force_delete or success)

    async def _download(self, message: Any, job_dir: Path, guard: StorageGuard, remaining: int) -> Path:
        reader = self.clients.require_reader()
        target = job_dir / mh.local_file_name(message)

        def progress(current: int, _total: int) -> None:
            guard.check_progress(remaining - current)

        result = await self.limiter.call(
            "download", reader.download_media, message, file=str(target), progress_callback=progress
        )
        if not result or not Path(result).exists():
            raise RetryableJobError("Telegram download produced no file", reason_code="network_error")
        return Path(result)

    async def _download_thumb(self, message: Any, job_dir: Path) -> Path | None:
        """Best effort: a missing or invalid preview never blocks the upload."""

        if mh.media_kind(message) not in {"video", "document", "audio", "animation"}:
            return None
        thumb = mh.thumbnail_candidate(message)
        if thumb is None:
            return None
        reader = self.clients.require_reader()
        target = job_dir / f".thumb-{message.id}.jpg"
        try:
            result = await self.limiter.call("download", reader.download_media, message, file=str(target), thumb=thumb)
            path = Path(result) if result else target
            if _valid_thumbnail(path):
                return path
        except Exception as exc:
            log.info("No thumbnail for message %s: %s", message.id, exc.__class__.__name__)
        return None

    def _caption(
        self, message: Any, transfer: TransferSettings, strip: bool
    ) -> tuple[str, list[Any] | None]:
        if transfer.drop_caption:
            return "", None
        text = mh.message_text(message)
        entities = getattr(message, "entities", None) or None
        if strip:
            entities = mh.visible_entities(entities)
        return text, entities

    async def _send_single(
        self,
        ctx: DeliveryContext,
        writer: TelegramClient,
        caps: AccountCapabilities,
        dest: Any,
        topic: int | None,
        item: tuple[Any, Path, Path | None],
        strip: bool,
    ) -> list[int]:
        message, path, thumb = item
        transfer = ctx.settings.transfer
        kind = mh.media_kind(message)
        caption, entities = self._caption(message, transfer, strip)
        overflow: tuple[str, list[Any] | None] | None = None
        if caption and utf16_len(caption) > caps.caption_limit:
            if transfer.caption_overflow == "truncate":
                caption, entities = truncate_utf16(caption, entities, caps.caption_limit)
            else:
                overflow, caption, entities = (caption, entities), "", None

        kwargs: dict[str, Any] = dict(
            caption=caption or None,
            formatting_entities=entities,
            parse_mode=None,
            reply_to=topic,
            force_document=kind == "document",
            voice_note=kind == "voice",
            video_note=kind == "video_note",
        )
        if kind != "photo":
            document = mh.media_object(message)
            kwargs["attributes"] = mh.upload_attributes(message)
            kwargs["mime_type"] = getattr(document, "mime_type", None)
            if thumb is not None:
                kwargs["thumb"] = str(thumb)
        try:
            result = await self.limiter.call("upload", writer.send_file, dest, str(path), **kwargs)
        except CAPTION_TOO_LONG_ERRORS:
            overflow, kwargs["caption"], kwargs["formatting_entities"] = (caption, entities), None, None
            result = await self.limiter.call("upload", writer.send_file, dest, str(path), **kwargs)

        messages = sent_messages(result)
        await self._check_topic(writer, dest, topic, messages)
        ids = [int(m.id) for m in messages]
        if overflow and overflow[0]:
            ids.extend(await self._send_overflow_text(writer, dest, topic, overflow))
        return ids

    async def _send_album(
        self,
        ctx: DeliveryContext,
        writer: TelegramClient,
        caps: AccountCapabilities,
        dest: Any,
        topic: int | None,
        items: list[tuple[Any, Path, Path | None]],
        strip: bool,
    ) -> list[int]:
        transfer = ctx.settings.transfer
        input_peer = await writer.get_input_entity(dest)
        multi_media = []
        overflow_texts: list[tuple[str, list[Any] | None]] = []
        for message, path, thumb in items:
            kind = mh.media_kind(message)
            uploaded = await self.limiter.call("upload", writer.upload_file, str(path))
            if kind == "photo":
                media: Any = types.InputMediaUploadedPhoto(file=uploaded)
            else:
                document = mh.media_object(message)
                uploaded_thumb = await writer.upload_file(str(thumb)) if thumb is not None else None
                media = build_request(
                    types.InputMediaUploadedDocument,
                    file=uploaded,
                    mime_type=getattr(document, "mime_type", None) or "application/octet-stream",
                    attributes=mh.upload_attributes(message),
                    thumb=uploaded_thumb,
                    force_file=True if kind == "document" else None,
                )
            stored = await self.limiter.call(
                "upload", writer, functions.messages.UploadMediaRequest(peer=input_peer, media=media)
            )
            caption, entities = self._caption(message, transfer, strip)
            if caption and utf16_len(caption) > caps.caption_limit:
                if transfer.caption_overflow == "truncate":
                    caption, entities = truncate_utf16(caption, entities, caps.caption_limit)
                else:
                    overflow_texts.append((caption, entities))
                    caption, entities = "", None
            multi_media.append(
                types.InputSingleMedia(
                    media=utils.get_input_media(stored),
                    random_id=helpers.generate_random_long(),
                    message=caption or "",
                    entities=entities,
                )
            )

        request = build_request(
            functions.messages.SendMultiMediaRequest,
            peer=input_peer,
            multi_media=multi_media,
            reply_to=reply_to_topic(topic),
        )
        result = await self.limiter.call("upload", writer, request)
        messages = sent_messages(result)
        if not messages:
            raise RetryableJobError("Telegram returned no album messages", reason_code="telegram_empty_result")
        await self._check_topic(writer, dest, topic, messages)
        ids = [int(m.id) for m in messages]
        for overflow in overflow_texts:
            ids.extend(await self._send_overflow_text(writer, dest, topic, overflow))
        return ids

    async def _send_overflow_text(
        self, writer: TelegramClient, dest: Any, topic: int | None, overflow: tuple[str, list[Any] | None]
    ) -> list[int]:
        text, entities = overflow
        result = await self.limiter.call(
            "upload", writer.send_message, dest, text, formatting_entities=entities, parse_mode=None, reply_to=topic
        )
        return [int(m.id) for m in sent_messages(result)]

    # ------------------------------------------------------------ utilities

    @staticmethod
    def _has_visible_spoiler(message: Any, transfer: TransferSettings) -> bool:
        keeps_text = mh.media_kind(message) == "text" or not transfer.drop_caption
        return mh.has_spoiler(message, include_text=keeps_text)

    async def _check_topic(self, writer: TelegramClient, dest: Any, topic: int | None, messages: list[Any]) -> None:
        if not topic:
            return
        if messages and all(mh.message_in_topic(message, topic) for message in messages):
            return
        ids = [int(m.id) for m in messages]
        if ids:
            try:
                await self.limiter.call("copy", writer.delete_messages, dest, ids)
            except Exception as exc:
                log.warning("Could not delete misplaced messages %s: %s", ids, exc)
        raise TopicDeliveryError(f"Messages did not arrive in topic {topic}; check the topic ID")

    async def _premium_alternative(
        self, transfer: TransferSettings, caps: AccountCapabilities, chat: str, largest: int
    ) -> tuple[TelegramClient, AccountCapabilities] | None:
        if not transfer.allow_premium_user_fallback or not caps.is_bot:
            return None
        reader_caps = self.clients.reader_capabilities(transfer.max_upload_bytes)
        reader = self.clients.reader
        if reader is None or reader_caps is None or reader_caps.account_type != "premium_user":
            return None
        if largest > reader_caps.max_upload_bytes:
            return None
        if not await self._reader_can_post(reader, chat):
            log.info("Premium fallback unavailable: the reading account cannot post to %s", chat)
            return None
        log.info("Using the Premium reading account for a %s-byte upload", largest)
        return reader, reader_caps

    async def _reader_can_post(self, reader: TelegramClient, chat: str) -> bool:
        try:
            entity = await self.clients.resolve(reader, chat)
            permissions = await self.limiter.call("resolve", reader.get_permissions, entity, "me")
        except Exception:
            return False
        if getattr(permissions, "is_banned", False) or getattr(permissions, "has_left", False):
            return False
        if isinstance(entity, types.Channel) and entity.broadcast:
            return bool(getattr(permissions, "is_creator", False) or (
                getattr(permissions, "is_admin", False) and getattr(permissions, "post_messages", True)
            ))
        return True


def _permanent_native_errors() -> tuple[type[BaseException], ...]:
    from app.telegram.errors_map import PERMISSION_ERRORS, SOURCE_MISSING_ERRORS

    return PERMISSION_ERRORS + SOURCE_MISSING_ERRORS
