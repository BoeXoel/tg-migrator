from __future__ import annotations

import pytest

from app.telegram.link_parser import parse_link, parse_request


@pytest.mark.parametrize(
    ("link", "chat", "topic", "message"),
    [
        ("https://t.me/somechannel/123", "@somechannel", None, 123),
        ("t.me/somechannel/123", "@somechannel", None, 123),
        ("https://t.me/s/somechannel/55", "@somechannel", None, 55),
        ("https://t.me/somegroup/456/789", "@somegroup", 456, 789),
        ("https://t.me/c/1234567890/321", "-1001234567890", None, 321),
        ("https://t.me/c/1234567890/456/321", "-1001234567890", 456, 321),
        ("https://t.me/c/1234567890/321?thread=456", "-1001234567890", 456, 321),
        ("tg://privatepost?channel=1234567890&post=9&thread=4", "-1001234567890", 4, 9),
        ("tg://resolve?domain=somechannel&post=7", "@somechannel", None, 7),
    ],
)
def test_parse_link(link, chat, topic, message):
    parsed = parse_link(link)
    assert parsed is not None
    assert (parsed.chat, parsed.topic_id, parsed.message_id) == (chat, topic, message)


@pytest.mark.parametrize(
    "link",
    [
        "https://t.me/+AbCdEf",
        "https://t.me/joinchat/AbCdEf",
        "https://example.com/c/1/2",
        "https://t.me/somechannel",
        "https://t.me/c/abc/1",
    ],
)
def test_rejects_non_message_links(link):
    assert parse_link(link) is None


def test_inline_range():
    parsed = parse_request("https://t.me/c/1234567890/100-500")
    assert (parsed.chat, parsed.start, parsed.end, parsed.count) == ("-1001234567890", 100, 500, 401)


def test_spaced_range_and_word_range():
    assert parse_request("https://t.me/c/1/100 - 200").end == 200
    assert parse_request("https://t.me/abcde/10 到 20").end == 20


def test_two_link_range_keeps_topic():
    parsed = parse_request("https://t.me/c/1/9/500\nhttps://t.me/c/1/9/100")
    assert (parsed.topic_id, parsed.start, parsed.end) == (9, 100, 500)


def test_two_links_from_different_chats_fail():
    with pytest.raises(ValueError):
        parse_request("https://t.me/c/1/100 https://t.me/c/2/200")


def test_single_link_is_not_range():
    parsed = parse_request("please copy https://t.me/somechannel/42 thanks")
    assert parsed.message_id == 42 and not parsed.is_range


def test_no_link():
    assert parse_request("hello") is None
