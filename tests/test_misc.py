from __future__ import annotations

import pytest

from app.bot.handlers_route import _parse_range, _parse_source
from app.bot.ui import target_id_list, yes_no
from app.engine.filters import ContentFilter, Rule, validate_rule
from app.logging import redact
from app.security import SecretBox, load_or_create_key


def test_content_filter():
    rules = [Rule(1, "keyword", "Sponsored"), Rule(2, "regex", r"https?://\S+")]
    cf = ContentFilter(rules, enabled=True, case_sensitive=False)
    assert cf.match_text("this is SPONSORED").rule_id == 1
    assert cf.match_texts([None, "see https://x.y"]).rule_type == "regex"
    assert cf.match_text("clean") is None
    assert ContentFilter(rules, enabled=False, case_sensitive=False).match_text("sponsored") is None
    with pytest.raises(ValueError):
        validate_rule("regex", "(")


def test_secret_box(tmp_path):
    box = SecretBox(load_or_create_key("", tmp_path))
    token = box.encrypt("hello")
    assert token != "hello" and box.decrypt(token) == "hello"
    assert box.encrypt(token) == token  # idempotent
    assert (tmp_path / "secret.key").stat().st_mode & 0o777 == 0o600
    other = SecretBox(load_or_create_key("a passphrase", tmp_path))
    with pytest.raises(ValueError):
        other.decrypt(token)


def test_log_redaction():
    text = redact("token 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw url https://user:pw@dav.example/x")
    assert "AAHdq" not in text and "pw@" not in text and "user:***@" in text


def test_bot_parsers():
    assert target_id_list("1, #2，3") == [1, 2, 3]
    with pytest.raises(ValueError):
        target_id_list("a")
    assert yes_no("是") is True and yes_no("n") is False
    assert _parse_range("100 - 5") == (5, 100)
    assert _parse_range("https://t.me/c/1/10-20") == (10, 20)
    assert _parse_source("https://t.me/c/1/9/100") == {"chat": "-1001", "topic_id": 9}
    assert _parse_source("-1002 7") == {"chat": "-1002", "topic_id": 7}
