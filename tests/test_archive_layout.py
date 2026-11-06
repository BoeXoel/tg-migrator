from __future__ import annotations

import json
from datetime import datetime

from telethon.tl import types

from app.config import RuntimeSettings, set_setting
from app.db.models import Job
from app.targets.base import ArchivePathBuilder, build_sidecars, sidecar_needed
from factories import doc_message, filename_attr, make, message, photo_message, video_attr


def _job(**values) -> Job:
    defaults = dict(
        id=1,
        source_chat_id="-1001234",
        source_title="My Channel",
        source_topic_id=None,
        source_message_id=10,
        grouped_id=None,
        media_type="video",
        message_date=datetime(2026, 11, 6, 8, 0),
    )
    defaults.update(values)
    return Job(**defaults)


def test_default_layout_is_flat():
    builder = ArchivePathBuilder(RuntimeSettings().archive)
    assert builder.directory_parts(_job()) == ["My Channel_-1001234"]


def test_topic_and_month_layers():
    settings = set_setting(RuntimeSettings(), "archive.group_by_month", True)
    builder = ArchivePathBuilder(settings.archive)
    assert builder.directory_parts(_job(source_topic_id=9)) == ["My Channel_-1001234", "topic_9", "2026-11"]


def test_folder_naming_modes():
    for mode, expected in (("id", "-1001234"), ("title", "My Channel")):
        settings = set_setting(RuntimeSettings(), "archive.folder_naming", mode)
        assert ArchivePathBuilder(settings.archive).source_folder(_job()) == expected
    assert ArchivePathBuilder(RuntimeSettings().archive).source_folder(_job(source_title="a/b:c")) == "a_b_c_-1001234"


def test_json_sidecar_for_caption():
    bold = make(types.MessageEntityBold, offset=0, length=5)
    spoiler = make(types.MessageEntitySpoiler, offset=6, length=5)
    msg = doc_message(10, text="Hello world", entities=[bold, spoiler], attributes=[video_attr(), filename_attr("v.mp4")])
    out = build_sidecars(_job(), [msg], {10: "10_v.mp4"}, RuntimeSettings())
    assert list(out) == ["10.json"]
    payload = json.loads(out["10.json"])
    assert payload["text"] == "Hello world"
    assert payload["items"][0]["file"] == "10_v.mp4"
    assert payload["entities"] == [{"type": "bold", "offset": 0, "length": 5}]  # spoiler removed
    assert payload["source"]["link"] == "https://t.me/c/1234/10"


def test_md_sidecar_and_text_only():
    settings = set_setting(RuntimeSettings(), "archive.sidecar_format", "md")
    out = build_sidecars(_job(media_type="text"), [message(10, text="plain *text*")], {}, settings)
    assert list(out) == ["10.md"]
    assert "plain *text*" in out["10.md"].decode()


def test_no_sidecar_for_media_without_caption():
    settings = RuntimeSettings()
    assert not sidecar_needed(_job(), [photo_message(10)], settings)
    assert sidecar_needed(_job(), [photo_message(10)], set_setting(settings, "archive.sidecar_always", True))
    assert build_sidecars(_job(), [photo_message(10, text="cap")], {}, set_setting(settings, "archive.sidecar_format", "none")) == {}


def test_drop_caption_skips_media_sidecar_but_keeps_text():
    settings = set_setting(RuntimeSettings(), "transfer.drop_caption", True)
    assert not sidecar_needed(_job(), [photo_message(10, text="cap")], settings)
    assert sidecar_needed(_job(), [message(10, text="text")], settings)
