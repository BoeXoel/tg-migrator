"""Fakes for Telegram clients used by target tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from telethon.tl import functions, types

from app.config import RuntimeSettings, load_bootstrap, set_setting
from app.telegram import media_helper as mh
from app.telegram.client_manager import AccountCapabilities
from app.telegram.limiter import TelegramLimiter
from factories import DATE, document, make, photo


def fast_settings(**overrides: Any) -> RuntimeSettings:
    settings = RuntimeSettings()
    for path in (
        "limits.global_min_delay_seconds",
        "limits.resolve_delay_seconds",
        "limits.read_delay_seconds",
        "limits.download_delay_seconds",
        "limits.copy_delay_seconds",
        "limits.upload_delay_seconds",
        "limits.verify_delay_seconds",
        "downloads.min_free_bytes",
    ):
        settings = set_setting(settings, path, 0)
    for path, value in overrides.items():
        settings = set_setting(settings, path.replace("__", "."), value)
    return settings


class FakeStore:
    def __init__(self, settings: RuntimeSettings) -> None:
        self.current = settings


def make_boot(tmp_path: Path):
    boot = load_bootstrap(
        {
            "API_ID": "1",
            "API_HASH": "x",
            "BOT_TOKEN": "1:x",
            "ADMIN_IDS": "1",
            "DATA_DIR": str(tmp_path / "data"),
            "SESSIONS_DIR": str(tmp_path / "sessions"),
            "DOWNLOADS_DIR": str(tmp_path / "downloads"),
            "ARCHIVE_ROOT": str(tmp_path / "archive"),
        }
    )
    boot.ensure_directories()
    boot.archive_root.mkdir(parents=True, exist_ok=True)
    return boot


class FakeReader:
    def __init__(self) -> None:
        self.downloads: list[int] = []

    async def download_media(self, message: Any, file: Any = None, progress_callback: Any = None, thumb: Any = None) -> Any:
        if thumb is not None:
            data = b"\xff\xd8\xff" + b"0" * 32
        else:
            data = b"d" * (mh.file_size(message) or 8)
            self.downloads.append(int(message.id))
        if file is bytes:
            return data
        Path(file).write_bytes(data)
        if progress_callback:
            progress_callback(len(data), len(data))
        return str(file)

    async def iter_download(self, doc: Any, request_size: int = 0):
        data = b"s" * int(doc.size)
        for index in range(0, len(data), 3):
            await asyncio.sleep(0)
            yield data[index : index + 3]


class FakeWriter:
    def __init__(self, topic_ok: bool = True) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []
        self.deleted: list[int] = []
        self.topic_ok = topic_ok
        self._next_id = 500

    def _sent(self, topic: int | None) -> Any:
        self._next_id += 1
        reply_to = make(types.MessageReplyHeader, reply_to_msg_id=topic, forum_topic=True) if topic and self.topic_ok else None
        return make(types.Message, id=self._next_id, peer_id=types.PeerChannel(9), date=DATE, message="", reply_to=reply_to)

    async def get_input_entity(self, entity: Any) -> Any:
        return types.InputPeerChannel(9, 9)

    async def send_message(self, entity: Any, text: str, **kwargs: Any) -> Any:
        self.calls.append(("send_message", text, kwargs))
        return self._sent(kwargs.get("reply_to"))

    async def send_file(self, entity: Any, file: str, **kwargs: Any) -> Any:
        self.calls.append(("send_file", file, kwargs))
        return self._sent(kwargs.get("reply_to"))

    async def upload_file(self, path: str) -> Any:
        self.calls.append(("upload_file", path, {}))
        return types.InputFile(id=1, parts=1, name=Path(path).name, md5_checksum="")

    async def delete_messages(self, entity: Any, ids: list[int]) -> None:
        self.deleted.extend(ids)

    async def __call__(self, request: Any) -> Any:
        self.calls.append((type(request).__name__, request, {}))
        if isinstance(request, functions.messages.UploadMediaRequest):
            if isinstance(request.media, types.InputMediaUploadedPhoto):
                return make(types.MessageMediaPhoto, photo=photo())
            return make(types.MessageMediaDocument, document=document())
        topic = None
        reply_to = getattr(request, "reply_to", None)
        if reply_to is not None:
            topic = reply_to.reply_to_msg_id
        count = len(getattr(request, "multi_media", None) or getattr(request, "id", None) or [1])
        updates = [
            make(types.UpdateNewChannelMessage, message=self._sent(topic or getattr(request, "top_msg_id", None)), pts=1, pts_count=1)
            for _ in range(count)
        ]
        return make(types.Updates, updates=updates, users=[], chats=[], date=DATE, seq=0)


class FakeClients:
    def __init__(self, boot: Any, *, writer_is_reader: bool = False, premium_reader: bool = False) -> None:
        self.boot = boot
        self.reader = FakeReader()
        self.writer = FakeWriter()
        self.bot = self.writer
        self.writer_is_reader = writer_is_reader
        self.premium_reader = premium_reader

    def require_reader(self) -> Any:
        return self.reader

    def writer_capabilities(self, override_limit: int = 0) -> AccountCapabilities:
        limit = override_limit or 2_000 * 1024 * 1024
        return AccountCapabilities("bot:1", "bot", 1, limit, 1024)

    def reader_capabilities(self, override_limit: int = 0) -> AccountCapabilities:
        kind = "premium_user" if self.premium_reader else "user"
        return AccountCapabilities(f"{kind}:2", kind, 2, override_limit or 4_000 * 1024 * 1024, 4096)

    async def resolve(self, client: Any, chat: Any) -> Any:
        return types.PeerChannel(9)


def limiter_for(settings: RuntimeSettings) -> TelegramLimiter:
    return TelegramLimiter(FakeStore(settings))
