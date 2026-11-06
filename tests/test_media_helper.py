from __future__ import annotations

from telethon.tl import types

from app.telegram import media_helper as mh
from factories import doc_message, filename_attr, make, message, photo_message, video_attr


def test_media_kinds():
    assert mh.media_kind(message(text="hi")) == "text"
    assert mh.media_kind(photo_message()) == "photo"
    assert mh.media_kind(doc_message(attributes=[video_attr()])) == "video"
    assert mh.media_kind(doc_message(attributes=[video_attr(round_message=True)])) == "video_note"
    assert mh.media_kind(doc_message(attributes=[make(types.DocumentAttributeAnimated), video_attr()])) == "animation"
    assert mh.media_kind(doc_message(attributes=[make(types.DocumentAttributeAudio, duration=3, voice=True)])) == "voice"
    assert mh.media_kind(doc_message(attributes=[make(types.DocumentAttributeAudio, duration=3, title="t")])) == "audio"
    sticker = make(types.DocumentAttributeSticker, alt="🙂", stickerset=types.InputStickerSetEmpty())
    assert mh.media_kind(doc_message(attributes=[sticker, video_attr()])) == "sticker"
    assert mh.media_kind(doc_message(attributes=[filename_attr("a.zip")])) == "document"
    assert mh.include_flag("poll") == "other"


def test_sizes_and_dedupe():
    video = doc_message(1, size=5000, doc_id=77, attributes=[video_attr()])
    picture = photo_message(2, photo_id=88)
    assert mh.file_size(video) == 5000
    assert mh.file_size(picture) == 50_000
    assert mh.group_file_size([video, picture]) == 55_000
    key = mh.dedupe_key("-1001", [video, picture])
    assert key == mh.dedupe_key("-1001", [doc_message(9, doc_id=77), photo_message(10, photo_id=88)])
    assert mh.dedupe_key("-1001", [message(1, text="a")]) != mh.dedupe_key("-1001", [message(2, text="a")])
    assert len(key) == 64


def test_spoilers_and_entities():
    spoiler = make(types.MessageEntitySpoiler, offset=0, length=2)
    bold = make(types.MessageEntityBold, offset=0, length=2)
    assert mh.has_spoiler(photo_message(spoiler=True))
    assert mh.has_spoiler(message(text="hi", entities=[spoiler]))
    assert not mh.has_spoiler(message(text="hi", entities=[spoiler]), include_text=False)
    assert mh.visible_entities([spoiler, bold]) == [bold]
    assert mh.visible_entities([spoiler]) is None


def test_topic_and_names():
    msg = doc_message(42, topic_id=7, attributes=[filename_attr("报告 2026:final?.pdf")], mime="application/pdf")
    assert mh.topic_of(msg) == 7
    assert mh.message_in_topic(msg, 7)
    assert mh.local_file_name(msg) == "42_报告 2026_final_.pdf"
    assert mh.local_file_name(photo_message(5)) == "5.jpg"
    assert mh.topic_of(message(1, text="x")) is None


def test_upload_attributes_drop_stream_hints():
    attr = video_attr(preload_prefix_size=1234, round_message=False)
    msg = doc_message(attributes=[attr, filename_attr("v.mp4")])
    cleaned = mh.upload_attributes(msg)
    video = next(a for a in cleaned if isinstance(a, types.DocumentAttributeVideo))
    assert (video.w, video.h, video.supports_streaming) == (1280, 720, True)
    assert getattr(video, "preload_prefix_size", None) is None
    assert any(isinstance(a, types.DocumentAttributeFilename) for a in cleaned)


def test_thumbnail_candidate_limits():
    small = make(types.PhotoSize, type="m", w=320, h=180, size=20_000)
    large = make(types.PhotoSize, type="x", w=800, h=450, size=90_000)
    huge = make(types.PhotoSize, type="s", w=90, h=90, size=300_000)
    msg = doc_message(attributes=[video_attr()], thumbs=[huge, large, small])
    assert mh.thumbnail_candidate(msg) is small
    assert mh.thumbnail_reservation(msg) == 20_000


def test_source_link():
    assert mh.source_link("-1001234", 5) == "https://t.me/c/1234/5"
    assert mh.source_link("-1001234", 5, 9) == "https://t.me/c/1234/9/5"
    assert mh.source_link("-1001234", 5, username="pub") == "https://t.me/pub/5"
