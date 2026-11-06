from __future__ import annotations

import asyncio

import pytest
import yaml

from app.backup import REDACTED, export_yaml, import_yaml
from app.settings_store import SettingsStore
from test_repository import make_repo


def test_export_redacts_and_import_keeps_password(tmp_path):
    async def scenario():
        repo = await make_repo(tmp_path)
        store = SettingsStore(repo)
        await store.load()
        await store.set("archive.sidecar_format", "md")
        dav = await repo.add_target(name="dav", type="webdav", config={"url": "https://dav.example/x", "username": "u", "password": "pw"})
        await repo.add_target(name="tg", type="telegram", config={"chat_id": "-1009"}, fallback_target_id=dav.id)
        await repo.add_route(name="r", source_chat_id="-1001", target_ids=[dav.id], watch_enabled=True, last_seen_msg_id=50)
        await repo.add_filter("keyword", "ad")
        exported = await export_yaml(repo, store)

        await store.set("archive.sidecar_format", "json")
        report = await import_yaml(exported, repo, store, archive_root=tmp_path)
        dav_after = await repo.get_target_by_name("dav")
        tg_after = await repo.get_target_by_name("tg")
        return exported, report, repo.decrypt_config(dav_after.config), tg_after, store.current, await repo.list_filters()

    exported, report, dav_config, tg_after, settings, rules = asyncio.run(scenario())
    document = yaml.safe_load(exported)
    assert document["targets"][0]["config"]["password"] == REDACTED
    assert b"pw" not in exported.split(b"password")[1][:20]
    assert dav_config["password"] == "pw"
    assert tg_after.fallback_target_id is not None
    assert settings.archive.sidecar_format == "md"
    assert report.targets_updated == 2 and report.routes_updated == 1 and report.filters == 1
    assert [r.pattern for r in rules] == ["ad"]


def test_invalid_import_changes_nothing(tmp_path):
    async def scenario():
        repo = await make_repo(tmp_path)
        store = SettingsStore(repo)
        await store.load()
        bad = b"version: 2\nsettings:\n  archive:\n    sidecar_format: xml\ntargets:\n  - name: x\n    type: local\n    config: {path: a}\n"
        with pytest.raises(ValueError):
            await import_yaml(bad, repo, store, archive_root=tmp_path)
        return await repo.list_targets(), store.current.archive.sidecar_format

    targets, fmt = asyncio.run(scenario())
    assert targets == [] and fmt == "json"


def test_replace_mode_removes_missing(tmp_path):
    async def scenario():
        repo = await make_repo(tmp_path)
        store = SettingsStore(repo)
        await store.load()
        await repo.add_target(name="old", type="local", config={"path": "old"})
        doc = b"version: 2\ntargets:\n  - name: new\n    type: local\n    config: {path: new}\n"
        report = await import_yaml(doc, repo, store, archive_root=tmp_path, replace=True)
        return report, [t.name for t in await repo.list_targets()]

    report, names = asyncio.run(scenario())
    assert names == ["new"] and report.targets_removed == 1


def test_local_target_escape_rejected(tmp_path):
    async def scenario():
        repo = await make_repo(tmp_path)
        store = SettingsStore(repo)
        await store.load()
        doc = b"version: 2\ntargets:\n  - name: bad\n    type: local\n    config: {path: ../../etc}\n"
        await import_yaml(doc, repo, store, archive_root=tmp_path / "archive")

    with pytest.raises(Exception):
        asyncio.run(scenario())
