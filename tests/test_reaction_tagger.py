from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from telethon import errors
from telethon.tl import types

from app.backup import export_yaml, import_yaml
from app.bot.context import BotState
from app.bot.handlers_config import (
    _tag_wizard,
    cmd_tag_del,
    cmd_tag_set,
    cmd_tag_toggle,
    config_view,
    tags_view,
)
from app.db.engine import create_engine, init_models, make_sessionmaker
from app.db.repository import Repository
from app.engine.notifier import Notifier
from app.engine.reaction_tagger import (
    ReactionTagger,
    apply_prefix_tags,
    compute_desired_tags,
    default_seed_triples,
    normalize_emoji,
    normalize_tag,
    parse_emoji_tag_pair,
    split_managed_prefix,
)
from app.security import SecretBox, load_or_create_key
from app.settings_store import SettingsStore
from app.targets.telegram_target import utf16_len
from app.telegram.limiter import TelegramLimiter
from factories import doc_message, make, message, photo_message
from fakes import make_boot


def test_emoji_and_tag_validation():
    # Variation selector normalization: ❤️ (\u2764\ufe0f) and ❤ (\u2764) share the exact same key
    disp1, key1 = normalize_emoji("❤️")
    disp2, key2 = normalize_emoji("❤")
    assert disp1 == "❤️" and disp2 == "❤"
    assert key1 == key2 == "❤"

    # Custom emoji entity extraction
    custom_ent = types.MessageEntityCustomEmoji(offset=0, length=2, document_id=5368324170671202286)
    c_disp, c_key = parse_emoji_tag_pair("🔥 tgm_custom", entities=[custom_ent])[:2]
    assert c_key == "custom:5368324170671202286"
    assert "5368324170671202286" in c_disp

    # Auto-# normalization & CJK/underscore support
    assert normalize_tag("tgm_fav") == "#tgm_fav"
    assert normalize_tag("#tgm_like") == "#tgm_like"
    assert normalize_tag("归档_精选1") == "#归档_精选1"

    # Reject invalid inputs
    for bad_tag in ("", "#", "#12345", "#bad-hyphen", "#two words", "#a#b"):
        with pytest.raises(ValueError):
            normalize_tag(bad_tag)

    for bad_emoji in ("", "abc", "#tgm_fav", "汉字", "❤️ 👍"):
        with pytest.raises(ValueError):
            normalize_emoji(bad_emoji)


def test_premium_three_emojis_fourth_eviction_and_utf16_entities():
    rules = [
        SimpleNamespace(id=1, emoji="❤️", emoji_key="❤", tag="#tgm_fav", enabled=True),
        SimpleNamespace(id=2, emoji="👍", emoji_key="👍", tag="#tgm_like", enabled=True),
        SimpleNamespace(id=3, emoji="🔥", emoji_key="🔥", tag="#tgm_hot", enabled=True),
        SimpleNamespace(id=4, emoji="⭐", emoji_key="⭐", tag="#tgm_star", enabled=True),
    ]
    managed = {r.tag for r in rules}

    # Original message starts with its own #原标签 AND contains supplementary-plane emoji 😀 + Bold + Link!
    orig_text = "#原标签 😀 重点内容 https://example.com"
    bold_offset = utf16_len("#原标签 😀 ")
    bold_len = utf16_len("重点内容")
    url_offset = utf16_len("#原标签 😀 重点内容 ")
    url_len = utf16_len("https://example.com")
    orig_entities = [
        types.MessageEntityHashtag(offset=0, length=utf16_len("#原标签")),
        types.MessageEntityBold(offset=bold_offset, length=bold_len),
        types.MessageEntityTextUrl(offset=url_offset, length=url_len, url="https://example.com"),
    ]

    def step(curr_text, curr_entities, old_keys, new_keys):
        existing, _, _ = split_managed_prefix(curr_text, managed)
        desired = compute_desired_tags(existing, rules, old_keys=old_keys, new_keys=new_keys)
        return apply_prefix_tags(curr_text, curr_entities, managed, desired)

    # 1) Premium reaction #1: ❤️
    t1, e1 = step(orig_text, orig_entities, [], ["❤"])
    assert t1 == "#tgm_fav #原标签 😀 重点内容 https://example.com"
    assert "\n" not in t1
    shift1 = utf16_len("#tgm_fav ")
    assert isinstance(e1[0], types.MessageEntityHashtag) and e1[0].offset == 0 and e1[0].length == utf16_len("#tgm_fav")
    bold1 = next(x for x in e1 if isinstance(x, types.MessageEntityBold))
    assert bold1.offset == bold_offset + shift1 and bold1.length == bold_len

    # 2) Premium reaction #2: ❤️ + 👍 (must accumulate, NOT replace!)
    t2, e2 = step(t1, e1, ["❤"], ["❤", "👍"])
    assert t2 == "#tgm_fav #tgm_like #原标签 😀 重点内容 https://example.com"

    # 3) Premium reaction #3: ❤️ + 👍 + 🔥 (all 3 tags present!)
    t3, e3 = step(t2, e2, ["❤", "👍"], ["❤", "👍", "🔥"])
    assert t3 == "#tgm_fav #tgm_like #tgm_hot #原标签 😀 重点内容 https://example.com"
    shift3 = utf16_len("#tgm_fav #tgm_like #tgm_hot ")
    url3 = next(x for x in e3 if isinstance(x, types.MessageEntityTextUrl))
    assert url3.offset == url_offset + shift3

    # 4) Premium reaction #4: ⭐ added -> Telegram evicts oldest ❤️ (old=[❤, 👍, 🔥], new=[👍, 🔥, ⭐])
    t4, e4 = step(t3, e3, ["❤", "👍", "🔥"], ["👍", "🔥", "⭐"])
    assert t4 == "#tgm_like #tgm_hot #tgm_star #原标签 😀 重点内容 https://example.com"

    # 5) Un-react 🔥 -> keeps 👍 and ⭐
    t5, e5 = step(t4, e4, ["👍", "🔥", "⭐"], ["👍", "⭐"])
    assert t5 == "#tgm_like #tgm_star #原标签 😀 重点内容 https://example.com"

    # 6) Un-react remaining 👍 and ⭐ -> restores exact original text & entity offsets without touching #原标签!
    t6, e6 = step(t5, e5, ["👍", "⭐"], [])
    assert t6 == orig_text
    assert [(type(x), x.offset, x.length) for x in e6] == [(type(x), x.offset, x.length) for x in orig_entities]

    # Empty media caption case: no trailing space when added, empty string when removed
    te1, _ = apply_prefix_tags("", None, managed, ["#tgm_fav"])
    assert te1 == "#tgm_fav"
    te2, ee2 = apply_prefix_tags(te1, [types.MessageEntityHashtag(offset=0, length=8)], managed, [])
    assert te2 == "" and ee2 == []


def test_reaction_tagger_end_to_end_album_and_guards(tmp_path):
    class FakeBot:
        def __init__(self, messages_by_id: dict[int, Any]):
            self.messages = messages_by_id
            self.edits: list[tuple[int, str, list[Any]]] = []
            self.fail_too_long = False

        async def get_messages(self, _peer: Any, *, ids: Any):
            if isinstance(ids, list):
                return [self.messages.get(i) for i in ids]
            return self.messages.get(int(ids))

        async def edit_message(
            self,
            _peer: Any,
            msg_id: int,
            text: str,
            *,
            parse_mode: Any = None,
            formatting_entities: Any = None,
            link_preview: bool = False,
        ):
            if self.fail_too_long:
                raise errors.MediaCaptionTooLongError(request=None)
            msg = self.messages[msg_id]
            msg.message = text
            msg.entities = formatting_entities
            self.edits.append((msg_id, text, formatting_entities))
            return msg

    async def scenario():
        boot = make_boot(tmp_path)  # admin_ids = {1}
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'rtag.db'}")
        try:
            await init_models(engine)
            repo = Repository(engine, make_sessionmaker(engine), SecretBox(load_or_create_key("", tmp_path)))
            store = SettingsStore(repo, default_language="zh")
            await store.load()
            for key in ("limits.global_min_delay_seconds", "limits.read_delay_seconds"):
                await store.set(key, 0)

            # Seed defaults once; deleting one must NOT come back on subsequent seed call
            seeded = await repo.seed_default_reaction_tags(default_seed_triples())
            assert seeded == 2
            assert await repo.seed_default_reaction_tags(default_seed_triples()) == 0

            await repo.add_target(name="ArchiveGroup", type="telegram", config={"chat_id": "-100555"})

            # Build fake messages in -100555 (PeerChannel(555))
            # Album [101, 102, 103] with grouped_id=9001: 101 has the album caption, 102 & 103 have ""
            m101 = photo_message(101, text="相册主文案", grouped_id=9001)
            m102 = photo_message(102, text="", grouped_id=9001)
            m103 = photo_message(103, text="", grouped_id=9001)
            # Sticker message 104 (non-captionable -> must be ignored)
            m104 = doc_message(
                104,
                mime="image/webp",
                attributes=[make(types.DocumentAttributeSticker, alt="😀")],
            )
            # Panel message 105 with inline buttons -> must be ignored
            m105 = photo_message(105, text="面板消息")
            m105.reply_markup = types.ReplyInlineMarkup(rows=[])
            # System notification message 106 -> must be ignored
            m106 = message(106, text="系统通知")

            for m in (m101, m102, m103, m104, m105, m106):
                m.from_id = types.PeerUser(user_id=777000)
                m.out = True

            fake_bot = FakeBot({m.id: m for m in (m101, m102, m103, m104, m105, m106)})
            clients = SimpleNamespace(bot=fake_bot, bot_me=SimpleNamespace(id=777000))
            notifier = Notifier((1,))
            notifier._sent_messages.append((-100555, 106))
            limiter = TelegramLimiter(store)
            tagger = ReactionTagger(boot, clients, limiter, repo, store, notifier)

            peer_target = types.PeerChannel(channel_id=555)
            now = datetime.now(timezone.utc)

            def make_upd(peer, msg_id, actor_uid, old_em, new_em):
                return types.UpdateBotMessageReaction(
                    peer=peer,
                    msg_id=msg_id,
                    date=now,
                    actor=types.PeerUser(user_id=actor_uid),
                    old_reactions=[types.ReactionEmoji(emoticon=e) for e in old_em],
                    new_reactions=[types.ReactionEmoji(emoticon=e) for e in new_em],
                    qts=1,
                )

            # 1) Non-admin user (uid=999) reacting is ignored
            assert await tagger.handle_reaction_update(make_upd(peer_target, 101, 999, [], ["❤"])) is False
            assert fake_bot.edits == []

            # 2) Private chat (PeerUser) is ignored
            assert await tagger.handle_reaction_update(make_upd(types.PeerUser(user_id=1), 101, 1, [], ["❤"])) is False

            # 3) Non-target group (PeerChannel(999)) is ignored
            assert await tagger.handle_reaction_update(make_upd(types.PeerChannel(channel_id=999), 101, 1, [], ["❤"])) is False

            # 4) Sticker (104), button panel (105), and notifier message (106) are ignored
            for ignored_id in (104, 105, 106):
                assert await tagger.handle_reaction_update(make_upd(peer_target, ignored_id, 1, [], ["❤"])) is False
            assert fake_bot.edits == []

            # 5) Admin reacts ❤️ on Album item #3 (103) -> updates Album caption carrier #1 (101)!
            assert await tagger.handle_reaction_update(make_upd(peer_target, 103, 1, [], ["❤"])) is True
            assert m101.message == "#tgm_fav 相册主文案"
            assert m103.message == ""
            # Record that 103 now holds ❤️ from admin 1 in recent_reactions
            m103.reactions = types.MessageReactions(
                results=[types.ReactionCount(reaction=types.ReactionEmoji(emoticon="❤"), count=1)],
                recent_reactions=[
                    types.MessagePeerReaction(
                        peer_id=types.PeerUser(user_id=1),
                        date=now,
                        reaction=types.ReactionEmoji(emoticon="❤"),
                    )
                ],
            )

            # 6) Admin ALSO reacts 👍 on Album item #1 (101) -> preserves ❤️ from 103 AND adds 👍!
            assert await tagger.handle_reaction_update(make_upd(peer_target, 101, 1, [], ["👍"])) is True
            assert m101.message == "#tgm_fav #tgm_like 相册主文案"

            # 7) Admin un-reacts 👍 on 101 -> removes #tgm_like while keeping #tgm_fav (still held on 103)!
            assert await tagger.handle_reaction_update(make_upd(peer_target, 101, 1, ["👍"], [])) is True
            assert m101.message == "#tgm_fav 相册主文案"

            # 8) If Telegram server rejects edit (e.g. MediaCaptionTooLongError), ignore gracefully without crashing
            fake_bot.fail_too_long = True
            assert await tagger.handle_reaction_update(make_upd(peer_target, 101, 1, [], ["👍"])) is False
            assert m101.message == "#tgm_fav 相册主文案"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_tags_console_wizard_commands_and_yaml_backup(tmp_path):
    async def scenario():
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'ui_tags.db'}")
        try:
            await init_models(engine)
            repo = Repository(engine, make_sessionmaker(engine), SecretBox(load_or_create_key("", tmp_path)))
            store = SettingsStore(repo, default_language="zh")
            await store.load()
            await repo.seed_default_reaction_tags(default_seed_triples())
            ctx = SimpleNamespace(
                repo=repo,
                store=store,
                lang="zh",
                state=BotState(),
                t=lambda en, zh: zh,
            )

            def cb_data(rows):
                return [
                    getattr(btn, "data", None)
                    or getattr(getattr(btn, "type", None), "data", None)
                    or getattr(getattr(btn, "button", None), "data", b"")
                    for row in rows
                    for btn in row
                ]

            # /config main menu includes 🏷 表情标签 (cfg:s:tags)
            _, main_rows = config_view(ctx, "main")
            main_cbs = cb_data(main_rows)
            assert b"cfg:s:tags" in main_cbs

            # tags_view displays seeded ❤️ -> #tgm_fav and 👍 -> #tgm_like
            t_text, t_rows = await tags_view(ctx)
            assert "#tgm_fav" in t_text and "#tgm_like" in t_text
            assert "/tag_set" in t_text and "/tag_del" in t_text
            t_cbs = cb_data(t_rows)
            assert b"cfg:t:tags.enabled" in t_cbs and b"cfg:tadd" in t_cbs

            # Wizard supports both 1-line ("🔥 #tgm_hot") and 2-step ("⭐" -> "tgm_star")
            replies: list[str] = []

            class DummyEvent:
                def __init__(self, text: str):
                    self.sender_id = 1
                    self.raw_text = text
                    self.message = SimpleNamespace(entities=None)
                    match = SimpleNamespace(group=lambda _i: text.split(None, 1)[1] if " " in text else "", lastindex=1)
                    self.pattern_match = match

                async def respond(self, text: str, **_kwargs: Any):
                    replies.append(text)

            await ctx.state.wizards.start(DummyEvent(""), _tag_wizard(ctx))
            await ctx.state.wizards.handle(DummyEvent("🔥 tgm_hot"))
            assert any("#tgm_hot" in r for r in replies)

            # 2-step wizard: emoji first, tag second
            await ctx.state.wizards.start(DummyEvent(""), _tag_wizard(ctx))
            await ctx.state.wizards.handle(DummyEvent("⭐"))
            assert ctx.state.wizards.has(1)  # waiting for step 2
            await ctx.state.wizards.handle(DummyEvent("#tgm_star"))
            assert not ctx.state.wizards.has(1)

            # /tag_set on existing ❤️ updates its tag in-place
            await cmd_tag_set(DummyEvent("/tag_set ❤️ #my_heart"), ctx)
            rules = await repo.list_reaction_tags()
            assert len(rules) == 4
            assert next(r.tag for r in rules if r.emoji_key == "❤") == "#my_heart"

            # /tag_toggle and /tag_del accept emoji directly or numeric ID
            await cmd_tag_toggle(DummyEvent("/tag_toggle 🔥"), ctx)
            assert next(r.enabled for r in await repo.list_reaction_tags() if r.emoji_key == "🔥") is False

            await cmd_tag_del(DummyEvent("/tag_del ⭐"), ctx)
            assert len(await repo.list_reaction_tags()) == 3

            # YAML export & import round-trip preserves reaction_tags and tags.enabled
            await store.set("tags.enabled", False)
            exported = await export_yaml(repo, store)
            doc = yaml.safe_load(exported)
            assert doc["settings"]["tags"]["enabled"] is False
            assert len(doc["reaction_tags"]) == 3

            await repo.delete_reaction_tag_by_key("❤")
            await store.set("tags.enabled", True)
            report = await import_yaml(exported, repo, store, archive_root=tmp_path)
            assert report.reaction_tags == 3
            assert store.current.tags.enabled is False
            restored = {r.emoji_key: (r.tag, r.enabled) for r in await repo.list_reaction_tags()}
            assert restored["❤"] == ("#my_heart", True)
            assert restored["🔥"] == ("#tgm_hot", False)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
