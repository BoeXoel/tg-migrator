from __future__ import annotations

import pytest

from app.config import (
    RuntimeSettings,
    dataclass_to_dict,
    get_setting,
    leaf_paths,
    load_bootstrap,
    set_setting,
    settings_from_sections,
)

BASE_ENV = {"API_ID": "1", "API_HASH": "abc", "BOT_TOKEN": "1:x", "ADMIN_IDS": "5, 6"}


def test_bootstrap_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    boot = load_bootstrap(BASE_ENV)
    assert boot.admin_ids == frozenset({5, 6})
    assert boot.notify_chat_ids == (5, 6)
    assert boot.database_url.startswith("sqlite+aiosqlite:///")
    assert boot.use_bot_as_writer


@pytest.mark.parametrize("missing", ["API_ID", "BOT_TOKEN", "ADMIN_IDS"])
def test_bootstrap_requires(missing):
    env = dict(BASE_ENV)
    env.pop(missing)
    with pytest.raises(ValueError):
        load_bootstrap(env)


def test_login_does_not_need_bot():
    env = {"API_ID": "1", "API_HASH": "abc"}
    assert load_bootstrap(env, require_bot=False).bot_token == ""


def test_set_and_get_settings():
    settings = RuntimeSettings()
    updated = set_setting(settings, "transfer.include.document", "yes")
    assert get_setting(updated, "transfer.include.document") is True
    assert get_setting(settings, "transfer.include.document") is False  # original untouched
    updated = set_setting(updated, "queue.retry_backoff_seconds", "60, 120")
    assert updated.queue.retry_backoff_seconds == [60, 120]
    assert set_setting(updated, "limits.copy_delay_seconds", "2.5").limits.copy_delay_seconds == 2.5


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("archive.sidecar_format", "xml"),
        ("worker.batch_size", "0"),
        ("worker.concurrency", "99"),
        ("transfer.hide_sender", "maybe"),
        ("transfer.unknown", "1"),
        ("transfer", "1"),
    ],
)
def test_invalid_settings(path, value):
    with pytest.raises((KeyError, ValueError)):
        set_setting(RuntimeSettings(), path, value)


def test_sections_roundtrip_and_strict():
    data = dataclass_to_dict(RuntimeSettings())
    assert settings_from_sections(data) == RuntimeSettings()
    data["archive"]["typo"] = True
    with pytest.raises(ValueError):
        settings_from_sections(data, strict=True)
    assert settings_from_sections(data).archive.sidecar_format == "json"


def test_defaults_match_requirements():
    settings = RuntimeSettings()
    assert settings.archive.sidecar_format == "json"
    assert settings.archive.group_by_month is False
    assert "archive.folder_naming" in leaf_paths()
