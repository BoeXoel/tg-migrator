from __future__ import annotations

import asyncio
import json
from datetime import datetime

import httpx
import pytest
from telethon.tl import types

from app.db.models import Job, Target
from app.errors import RemotePermissionError, TargetConfigError, TopicDeliveryError
from app.targets.base import DeliveryContext, DeliveryResult
from app.targets.local_target import LocalTarget, resolve_local_root
from app.targets.telegram_target import TelegramTarget, build_request, sent_messages, truncate_utf16, utf16_len
from app.targets.webdav_target import WebDAVClient, WebDAVTarget, join_url, validate_webdav_url
from factories import doc_message, filename_attr, make, message, photo_message, video_attr
from fakes import FakeClients, fast_settings, limiter_for, make_boot


class Flag:
    def is_set(self) -> bool:
        return False


async def noop_phase(_phase: str) -> None:
    return None


def make_job(**values) -> Job:
    defaults = dict(
        id=7,
        target_id=1,
        source_chat_id="-1001234",
        source_title="My Channel",
        source_topic_id=None,
        source_message_id=10,
        source_message_ids=[10],
        grouped_id=None,
        dedupe_key="k",
        media_type="video",
        message_date=datetime(2026, 11, 6),
    )
    defaults.update(values)
    return Job(**defaults)


def context(job, target, config, messages, settings) -> DeliveryContext:
    return DeliveryContext(
        job=job,
        target=target,
        target_config=config,
        messages=messages,
        settings=settings,
        stop_event=Flag(),
        on_phase=noop_phase,
        source_entity=types.PeerChannel(1234),
    )


def video(msg_id=10, size=64, text="caption", **kwargs):
    return doc_message(
        msg_id, size=size, text=text, mime="video/mp4", attributes=[video_attr(), filename_attr("clip.mp4")], **kwargs
    )


# --------------------------------------------------------------------- local


def test_local_path_must_stay_inside_root(tmp_path):
    assert resolve_local_root(tmp_path, "a/b") == (tmp_path / "a" / "b").resolve()
    with pytest.raises(TargetConfigError):
        resolve_local_root(tmp_path, "../escape")


def test_local_target_writes_files_and_sidecar(tmp_path):
    boot = make_boot(tmp_path)
    clients = FakeClients(boot)
    settings = fast_settings()
    handler = LocalTarget(boot, clients, limiter_for(settings))
    target = Target(id=1, name="nas", type="local", enabled=True, config={"path": "tg"})
    result = asyncio.run(handler.deliver(context(make_job(), target, {"path": "tg"}, [video()], settings)))
    folder = boot.archive_root / "tg" / "My Channel_-1001234"
    assert result.status == "done" and result.transfer_route == "local"
    assert (folder / "10_clip.mp4").read_bytes() == b"d" * 64
    assert json.loads((folder / "10.json").read_text())["text"] == "caption"
    assert not list(folder.glob("*.part"))


# -------------------------------------------------------------------- webdav


def test_webdav_url_rules():
    assert validate_webdav_url("http://openlist:5244/dav/") == "http://openlist:5244/dav"
    with pytest.raises(TargetConfigError):
        validate_webdav_url("ftp://x/dav")
    with pytest.raises(TargetConfigError):
        validate_webdav_url("https://user:pw@x/dav")
    assert join_url("http://h/dav", ["中文 dir", "a#b.mp4"]) == "http://h/dav/%E4%B8%AD%E6%96%87%20dir/a%23b.mp4"


def _recording_transport(log, status_for=None):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        # raw_path: the percent-encoded path actually sent on the wire
        log.append((request.method, request.url.raw_path.decode("ascii"), dict(request.headers), body))
        status = (status_for or {}).get(request.method, 201)
        return httpx.Response(status)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize("mode", ["stream", "spool"])
def test_webdav_target_uploads(tmp_path, mode):
    boot = make_boot(tmp_path)
    settings = fast_settings(**{"archive__webdav_mode": mode})
    handler = WebDAVTarget(boot, FakeClients(boot), limiter_for(settings))
    requests: list = []
    handler.transport = _recording_transport(requests)
    config = {"url": "http://openlist:5244/dav", "username": "u", "password": "p", "base_path": "tg"}
    target = Target(id=2, name="dav", type="webdav", enabled=True, config=config)
    result = asyncio.run(handler.deliver(context(make_job(), target, config, [video(size=10)], settings)))

    assert result.status == "done"
    methods = [(m, p) for m, p, _h, _b in requests]
    assert ("MKCOL", "/dav/tg/") in methods
    assert ("MKCOL", "/dav/tg/My%20Channel_-1001234/") in methods
    put_video = next(r for r in requests if r[0] == "PUT" and r[1].endswith("10_clip.mp4"))
    assert put_video[2]["content-length"] == "10"
    assert put_video[3] == (b"s" * 10 if mode == "stream" else b"d" * 10)
    assert any(r[1].endswith("10.json") for r in requests if r[0] == "PUT")
    assert put_video[2]["authorization"].startswith("Basic ")
    assert not list(boot.active_dir.iterdir())  # spool directory cleaned


def test_webdav_error_mapping(tmp_path):
    boot = make_boot(tmp_path)

    async def scenario(status):
        async with WebDAVClient({"url": "https://dav.example/x"}, boot, transport=_recording_transport([], {"PUT": status})) as dav:
            await dav.put_bytes(["f"], b"1")

    with pytest.raises(RemotePermissionError):
        asyncio.run(scenario(401))
    with pytest.raises(TargetConfigError):
        asyncio.run(scenario(409))
    from app.errors import RemoteTransferError

    with pytest.raises(RemoteTransferError):
        asyncio.run(scenario(507))


# ------------------------------------------------------------------ telegram


def test_utf16_truncation_trims_entities():
    bold = make(types.MessageEntityBold, offset=2, length=6)
    text, entities = truncate_utf16("ab😀cdefgh", [bold], 5)
    assert utf16_len(text) <= 5 and text == "ab😀c"
    assert entities[0].length == 3


def test_build_request_ignores_unknown_and_none():
    from telethon.tl import functions

    request = build_request(functions.messages.ForwardMessagesRequest, from_peer=types.InputPeerSelf(), id=[1], random_id=[2],
                            to_peer=types.InputPeerSelf(), drop_author=None, not_a_field=1)
    assert request.id == [1] and request.drop_author is None


def test_sent_messages_from_updates():
    msg = make(types.Message, id=5, peer_id=types.PeerChannel(1), date=datetime(2026, 1, 1), message="")
    updates = make(types.Updates, updates=[make(types.UpdateNewChannelMessage, message=msg, pts=1, pts_count=1)], users=[], chats=[], date=datetime(2026, 1, 1), seq=0)
    assert [m.id for m in sent_messages(updates)] == [5]


def _telegram(tmp_path, settings, fallback=None, **client_kwargs):
    boot = make_boot(tmp_path)
    clients = FakeClients(boot, **client_kwargs)

    async def no_fallback(_ctx):
        return None

    return TelegramTarget(boot, clients, limiter_for(settings), fallback or no_fallback), clients, boot


def _tg_target(topic=None, fallback_id=None):
    config = {"chat_id": "-1009", "topic_id": topic}
    return Target(id=1, name="tg", type="telegram", enabled=True, config=config, fallback_target_id=fallback_id), config


def test_text_is_sent_without_markdown_parsing(tmp_path):
    settings = fast_settings()
    handler, clients, _ = _telegram(tmp_path, settings)
    target, config = _tg_target()
    msg = message(10, text="**not bold**")
    result = asyncio.run(handler.deliver(context(make_job(media_type="text"), target, config, [msg], settings)))
    name, text, kwargs = clients.writer.calls[0]
    assert result.status == "done" and result.transfer_route == "tg_text"
    assert name == "send_message" and text == "**not bold**" and kwargs["parse_mode"] is None


def test_video_reupload_keeps_metadata_and_cleans_up(tmp_path):
    settings = fast_settings()
    handler, clients, boot = _telegram(tmp_path, settings)
    target, config = _tg_target(topic=33)
    thumb = make(types.PhotoSize, type="m", w=320, h=180, size=1000)
    msg = doc_message(10, size=64, text="cap", mime="video/mp4", attributes=[video_attr(), filename_attr("clip.mp4")], thumbs=[thumb])
    result = asyncio.run(handler.deliver(context(make_job(), target, config, [msg], settings)))
    call = next(c for c in clients.writer.calls if c[0] == "send_file")
    kwargs = call[2]
    assert result.status == "done" and result.transfer_route == "tg_upload"
    assert kwargs["caption"] == "cap" and kwargs["reply_to"] == 33 and kwargs["mime_type"] == "video/mp4"
    assert any(isinstance(a, types.DocumentAttributeVideo) for a in kwargs["attributes"])
    assert kwargs["thumb"].endswith(".jpg")
    assert not list(boot.active_dir.iterdir())


def test_album_reupload_uses_per_item_captions(tmp_path):
    settings = fast_settings()
    handler, clients, _ = _telegram(tmp_path, settings)
    target, config = _tg_target()
    items = [photo_message(10, text="first", grouped_id=5), video(11, text="second", grouped_id=5)]
    result = asyncio.run(handler.deliver(context(make_job(source_message_ids=[10, 11]), target, config, items, settings)))
    request = next(c[1] for c in clients.writer.calls if c[0] == "SendMultiMediaRequest")
    assert [m.message for m in request.multi_media] == ["first", "second"]
    assert result.dest_message_ids and len(result.dest_message_ids) == 2


def test_oversized_uses_fallback_or_records(tmp_path):
    settings = fast_settings(**{"transfer__max_upload_bytes": 10})
    target, config = _tg_target(fallback_id=3)
    msg = video(size=100)

    async def fallback(_ctx):
        return DeliveryResult("done", transfer_route="fallback:webdav", reason_code="oversized_fallback")

    handler, clients, _ = _telegram(tmp_path, settings, fallback=fallback)
    assert asyncio.run(handler.deliver(context(make_job(), target, config, [msg], settings))).transfer_route == "fallback:webdav"

    handler, clients, _ = _telegram(tmp_path, settings)
    result = asyncio.run(handler.deliver(context(make_job(), target, config, [msg], settings)))
    assert result.status == "skipped" and result.reason_code == "oversized"
    assert clients.reader.downloads == []  # never downloaded


def test_spoiler_with_native_only_is_skipped(tmp_path):
    settings = fast_settings(**{"transfer__native_only": True})
    handler, _clients, _ = _telegram(tmp_path, settings, writer_is_reader=True)
    target, config = _tg_target()
    result = asyncio.run(handler.deliver(context(make_job(), target, config, [photo_message(spoiler=True)], settings)))
    assert result.reason_code == "spoiler_removal_requires_reupload"


def test_native_forward_when_reader_writes(tmp_path):
    settings = fast_settings()
    handler, clients, _ = _telegram(tmp_path, settings, writer_is_reader=True)
    target, config = _tg_target(topic=4)
    result = asyncio.run(handler.deliver(context(make_job(), target, config, [video()], settings)))
    request = next(c[1] for c in clients.writer.calls if c[0] == "ForwardMessagesRequest")
    assert result.transfer_route == "tg_copy" and request.drop_author is True
    assert clients.reader.downloads == []


def test_misplaced_topic_is_deleted(tmp_path):
    settings = fast_settings()
    handler, clients, _ = _telegram(tmp_path, settings)
    clients.writer.topic_ok = False
    target, config = _tg_target(topic=4)
    with pytest.raises(TopicDeliveryError):
        asyncio.run(handler.deliver(context(make_job(media_type="text"), target, config, [message(10, text="x")], settings)))
    assert clients.writer.deleted


def test_limiter_operations_do_not_block_each_other():
    import time

    settings = fast_settings(
        **{
            "limits__upload_delay_seconds": 0.25,
            "limits__read_delay_seconds": 0.0,
            "limits__download_delay_seconds": 0.0,
        }
    )
    limiter = limiter_for(settings)

    async def scenario():
        # Prime upload so the next upload must sleep 0.25s
        await limiter.wait("upload")
        t0 = time.monotonic()

        async def slow_upload():
            await limiter.wait("upload")
            return time.monotonic() - t0

        async def fast_read_and_download():
            await asyncio.sleep(0.01)
            await limiter.wait("read")
            await limiter.wait("download")
            return time.monotonic() - t0

        up_elapsed, rd_elapsed = await asyncio.gather(slow_upload(), fast_read_and_download())
        return up_elapsed, rd_elapsed

    up_elapsed, rd_elapsed = asyncio.run(scenario())
    assert rd_elapsed < 0.12
    assert up_elapsed >= 0.20
