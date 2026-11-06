from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telethon.tl import types

from app.bot.handlers_config import config_view
from app.bot.handlers_route import _parse_range, _parse_source, routes_view
from app.bot.handlers_target import targets_view
from app.bot.ui import extract_secret_text, target_id_list, yes_no
from app.db.engine import create_engine, init_models, make_sessionmaker
from app.db.repository import Repository
from app.engine.filters import ContentFilter, Rule, validate_rule
from app.logging import redact
from app.security import SecretBox, load_or_create_key
from app.settings_store import SettingsStore


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


def test_extract_secret_text_protects_markdown_symbols():
    # Plain password without entities
    evt_plain = SimpleNamespace(raw_text="p@ss!123", message=SimpleNamespace(entities=None))
    assert extract_secret_text(evt_plain) == "p@ss!123"

    # Password wrapped in `code` entity (Telegram preserves _, *, ~ inside code)
    evt_code = SimpleNamespace(
        raw_text="p@ss_w0rd*123~",
        message=SimpleNamespace(entities=[types.MessageEntityCode(offset=0, length=14)]),
    )
    assert extract_secret_text(evt_code) == "p@ss_w0rd*123~"

    # Password wrapped in literal backticks when client didn't create entity
    evt_literal_ticks = SimpleNamespace(raw_text="`p@ss_w0rd_123`", message=SimpleNamespace(entities=[]))
    assert extract_secret_text(evt_literal_ticks) == "p@ss_w0rd_123"

    # Password where Telegram ate _ or * into Italic/Bold entity -> must reject and warn
    evt_italic = SimpleNamespace(
        raw_text="p@ssw0rd123",
        message=SimpleNamespace(entities=[types.MessageEntityItalic(offset=4, length=4)]),
    )
    with pytest.raises(ValueError, match="backticks"):
        extract_secret_text(evt_italic, lang="en")
    with pytest.raises(ValueError, match="反引号"):
        extract_secret_text(evt_italic, lang="zh")


def test_subpanels_have_back_to_main_menu_and_bilingual_labels(tmp_path):
    async def scenario():
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        try:
            await init_models(engine)
            repo = Repository(engine, make_sessionmaker(engine), SecretBox(load_or_create_key("", tmp_path)))
            store = SettingsStore(repo)
            await store.load()
            ctx = SimpleNamespace(repo=repo, store=store, lang="en")
            def has_home(rows, expected_label):
                return any(
                    expected_label in getattr(btn, "text", str(btn))
                    for row in rows
                    for btn in row
                )

            for lang in ("en", "zh"):
                await store.set("ui.language", lang)
                ctx.lang = lang
                _, tg_rows = await targets_view(ctx)
                _, rt_rows = await routes_view(ctx)
                cfg_text, cfg_rows = config_view(ctx, "main")
                expected = "Main Menu" if lang == "en" else "主菜单"
                assert has_home(tg_rows, expected)
                assert has_home(rt_rows, expected)
                assert has_home(cfg_rows, expected)
                if lang == "en":
                    assert "Settings" in cfg_text
                else:
                    assert "设置" in cfg_text
        finally:
            await engine.dispose()

    asyncio.run(scenario())
