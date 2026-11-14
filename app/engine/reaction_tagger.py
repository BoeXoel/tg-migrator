"""Bi-directional Emoji reaction -> leading #TAG synchronizer for Bot-delivered archive messages."""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from typing import Any, Iterable, Sequence

from telethon import TelegramClient, events, utils
from telethon.tl import types

from app.config import BootstrapConfig
from app.db.models import ReactionTagRule
from app.db.repository import Repository
from app.engine.notifier import Notifier
from app.settings_store import SettingsStore
from app.targets.telegram_target import utf16_len
from app.telegram import media_helper as mh
from app.telegram.client_manager import ClientManager, canonical_id
from app.telegram.errors_map import BAD_REQUEST_ERRORS, CAPTION_TOO_LONG_ERRORS, PERMISSION_ERRORS, SOURCE_MISSING_ERRORS
from app.telegram.limiter import TelegramLimiter

log = logging.getLogger("migrator.reaction_tagger")

DEFAULT_REACTION_TAGS: tuple[tuple[str, str], ...] = (
    ("❤️", "#tgm_fav"),
    ("👍", "#tgm_like"),
)

EDITABLE_MEDIA_KINDS = frozenset({"text", "photo", "video", "document", "audio", "voice", "animation"})
_VARIATION_SELECTORS_RE = re.compile(r"[\ufe0e\ufe0f]")
_LEADING_HASH_TOKEN_RE = re.compile(r"#[^\s]+")
_SPACES_RE = re.compile(r" +")
_TAG_BODY_RE = re.compile(r"^[\w]+$", re.UNICODE)


def normalize_emoji(raw: str, *, entities: Sequence[Any] | None = None, offset: int = 0) -> tuple[str, str]:
    """Validate and normalize an emoji string into ``(display_emoji, emoji_key)``."""

    text = (raw or "").strip()
    if not text:
        raise ValueError("Emoji cannot be empty")
    if any(ch.isspace() for ch in text):
        raise ValueError("Emoji must not contain spaces")

    if text.lower().startswith("custom:"):
        doc_part = text.split(":", 1)[1].strip()
        if not doc_part.isdigit():
            raise ValueError("Custom emoji format must be custom:<numeric_document_id>")
        key = f"custom:{int(doc_part)}"
        return key, key

    for ent in entities or ():
        if isinstance(ent, types.MessageEntityCustomEmoji):
            ent_start = int(getattr(ent, "offset", -1))
            ent_end = ent_start + int(getattr(ent, "length", 0))
            if ent_start <= offset < ent_end:
                doc_id = int(ent.document_id)
                return f"{text} (custom:{doc_id})", f"custom:{doc_id}"

    key = _VARIATION_SELECTORS_RE.sub("", text)
    if not key or len(key) > 16:
        raise ValueError("Please provide a single valid emoji")
    has_emoji_symbol = any((not ch.isalnum()) and ord(ch) > 127 for ch in key)
    has_non_ascii_word = any(ch.isalnum() and ord(ch) > 127 for ch in key)
    if not has_emoji_symbol or has_non_ascii_word:
        raise ValueError("First argument must be an emoji (e.g. ❤️, 👍, 🔥)")
    return text, key


def normalize_tag(raw: str) -> str:
    """Validate and normalize a hashtag token (auto-prepending ``#`` if omitted)."""

    cleaned = (raw or "").strip()
    body = cleaned.lstrip("#")
    if not body:
        raise ValueError("Tag cannot be empty")
    if len(body) > 64:
        raise ValueError("Tag must be 64 characters or fewer")
    if any(ch.isspace() for ch in body):
        raise ValueError("Tag cannot contain spaces")
    if "-" in body:
        raise ValueError("Telegram hashtags break at '-'; please use '_' instead (e.g. #tgm_fav)")
    if not _TAG_BODY_RE.fullmatch(body) or body.isdigit():
        raise ValueError("Tag may only contain letters, digits, and underscores, and cannot be purely numeric")
    return f"#{body}"


def parse_emoji_tag_pair(raw: str, *, entities: Sequence[Any] | None = None) -> tuple[str, str, str]:
    """Parse ``<emoji> <tag>`` (with optional ``/tag_set`` command prefix) into ``(emoji, emoji_key, tag)``."""

    text = raw or ""
    stripped = text.strip()
    for prefix in ("/tag_set", "/tag_add"):
        if stripped.lower().startswith(prefix):
            stripped = stripped[len(prefix) :].strip()
            break
    parts = stripped.split(None, 1)
    if len(parts) < 2:
        raise ValueError("Format: <emoji> <tag> (for example: 🔥 #tgm_hot)")
    emoji_token, tag_token = parts[0], parts[1].strip()
    token_char_index = text.find(emoji_token)
    utf16_offset = utf16_len(text[:token_char_index]) if token_char_index >= 0 else 0
    emoji, emoji_key = normalize_emoji(emoji_token, entities=entities, offset=utf16_offset)
    tag = normalize_tag(tag_token)
    return emoji, emoji_key, tag


def default_seed_triples() -> list[tuple[str, str, str]]:
    triples: list[tuple[str, str, str]] = []
    for emoji, tag in DEFAULT_REACTION_TAGS:
        disp, key = normalize_emoji(emoji)
        triples.append((disp, key, normalize_tag(tag)))
    return triples


def reaction_key(reaction: Any) -> str | None:
    """Extract normalized ``emoji_key`` from a Telethon ``TypeReaction`` object."""

    if isinstance(reaction, types.ReactionEmoji):
        emoticon = getattr(reaction, "emoticon", "") or ""
        cleaned = _VARIATION_SELECTORS_RE.sub("", emoticon.strip())
        return cleaned or None
    if isinstance(reaction, types.ReactionCustomEmoji):
        doc_id = getattr(reaction, "document_id", None)
        return f"custom:{int(doc_id)}" if doc_id is not None else None
    return None


def split_managed_prefix(text: str, managed_tags: set[str]) -> tuple[list[str], str, int]:
    """Split leading managed ``#TAG`` tokens from ``text``.

    Returns ``(existing_managed_tags, remaining_body, old_prefix_utf16_len)``.
    Hashtags that are not in ``managed_tags`` (such as original source hashtags)
    are never consumed and remain part of ``remaining_body``.
    """

    if not text or not managed_tags:
        return [], text or "", 0
    existing: list[str] = []
    pos = 0
    total = len(text)
    while pos < total:
        match = _LEADING_HASH_TOKEN_RE.match(text[pos:])
        if not match:
            break
        token = match.group(0)
        if token not in managed_tags:
            break
        existing.append(token)
        end = pos + len(token)
        spaces = _SPACES_RE.match(text[end:])
        if spaces:
            pos = end + len(spaces.group(0))
        else:
            pos = end
            break
    body = text[pos:]
    return existing, body, utf16_len(text[:pos])


def compute_desired_tags(
    existing_tags: Sequence[str],
    rules: Sequence[ReactionTagRule],
    *,
    old_keys: Sequence[str],
    new_keys: Sequence[str],
    other_active_keys: Iterable[str] = (),
) -> list[str]:
    """Reconcile message tags against multi-emoji reactions (including Premium 3-emoji & 4th-emoji eviction)."""

    enabled_map = {rule.emoji_key: rule.tag for rule in rules if rule.enabled}
    all_map = {rule.emoji_key: rule.tag for rule in rules}

    new_set = set(new_keys)
    still_held_keys = new_set | set(other_active_keys)

    dropped_keys = [key for key in old_keys if key not in still_held_keys]
    tags_still_held = {enabled_map[key] for key in still_held_keys if key in enabled_map}
    tags_to_remove = {all_map[key] for key in dropped_keys if key in all_map} - tags_still_held

    result: list[str] = []
    for tag in existing_tags:
        if tag not in tags_to_remove and tag not in result:
            result.append(tag)

    for key in new_keys:
        tag = enabled_map.get(key)
        if tag and tag not in result:
            result.append(tag)

    return result


def apply_prefix_tags(
    text: str,
    entities: Sequence[Any] | None,
    managed_tags: set[str],
    desired_tags: Sequence[str],
) -> tuple[str, list[Any]]:
    """Build updated message text (single-space separated, no newline) and UTF-16 shifted entities."""

    _, body, old_prefix_len = split_managed_prefix(text or "", managed_tags)
    unique_desired: list[str] = []
    for tag in desired_tags:
        if tag not in unique_desired:
            unique_desired.append(tag)

    if not unique_desired:
        new_prefix = ""
    elif body:
        new_prefix = " ".join(unique_desired) + " "
    else:
        new_prefix = " ".join(unique_desired)

    new_text = new_prefix + body
    new_prefix_len = utf16_len(new_prefix)
    delta = new_prefix_len - old_prefix_len

    prefix_entities: list[Any] = []
    cursor = 0
    for tag in unique_desired:
        tlen = utf16_len(tag)
        prefix_entities.append(types.MessageEntityHashtag(offset=cursor, length=tlen))
        cursor += tlen + 1

    shifted_body_entities: list[Any] = []
    for ent in entities or ():
        ent_offset = int(getattr(ent, "offset", 0))
        if ent_offset < old_prefix_len:
            continue
        cloned = copy.copy(ent)
        cloned.offset = ent_offset + delta
        shifted_body_entities.append(cloned)

    return new_text, prefix_entities + shifted_body_entities


def _candidate_peer_ids(peer: Any) -> set[str]:
    """Return both Telethon marked peer ID and Bot-API ``-100<id>`` form (which differ on <10-digit channel IDs)."""

    ids = {str(int(utils.get_peer_id(peer)))}
    if isinstance(peer, types.PeerChannel):
        ids.add(f"-100{int(peer.channel_id)}")
    elif isinstance(peer, types.PeerChat):
        ids.add(f"-{int(peer.chat_id)}")
    return ids


class ReactionTagger:
    """Listens for admin reactions on Bot-delivered target messages and syncs leading #TAGs."""

    def __init__(
        self,
        boot: BootstrapConfig,
        clients: ClientManager,
        limiter: TelegramLimiter,
        repo: Repository,
        store: SettingsStore,
        notifier: Notifier,
    ) -> None:
        self.boot = boot
        self.clients = clients
        self.limiter = limiter
        self.repo = repo
        self.store = store
        self.notifier = notifier
        self._locks: dict[tuple[str, int], asyncio.Lock] = {}
        self._resolved_target_ids: dict[str, str] = {}
        self._registered_on: TelegramClient | None = None

    def register(self, bot: TelegramClient) -> None:
        if self._registered_on is not bot:
            bot.add_event_handler(self._on_raw_reaction, events.Raw(types=types.UpdateBotMessageReaction))
            self._registered_on = bot

    def _lock_for(self, chat_id: str, msg_id: int) -> asyncio.Lock:
        key = (chat_id, msg_id)
        if key not in self._locks:
            if len(self._locks) > 1024:
                # Evict unlocked entries to bound memory in long-running containers.
                for old_key in [k for k, lk in self._locks.items() if not lk.locked()][:512]:
                    self._locks.pop(old_key, None)
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def _is_configured_target_peer(self, bot: TelegramClient, candidate_ids: set[str]) -> bool:
        targets = await self.repo.list_targets()
        tg_chats = [
            str(t.config.get("chat_id") or "").strip()
            for t in targets
            if t.type == "telegram" and t.config.get("chat_id")
        ]
        if not tg_chats:
            return False
        if any(cid in tg_chats for cid in candidate_ids):
            return True
        for raw_chat in tg_chats:
            cache_key = raw_chat.lower()
            resolved = self._resolved_target_ids.get(cache_key)
            if resolved is None and not raw_chat.lstrip("-").isdigit():
                try:
                    entity = await self.clients.resolve(bot, raw_chat)
                    resolved = canonical_id(entity)
                    self._resolved_target_ids[cache_key] = resolved
                except Exception:
                    continue
            if resolved in candidate_ids:
                return True
        return False

    def _is_bot_authored_archive_message(self, message: Any, candidate_ids: set[str], bot_id: int | None) -> bool:
        if message is None or isinstance(message, types.MessageService):
            return False
        if getattr(message, "reply_markup", None) is not None:
            return False
        msg_id_int = int(message.id)
        for cid in candidate_ids:
            if cid.lstrip("-").isdigit() and self.notifier.is_system_message(int(cid), msg_id_int):
                return False
        sender_id = getattr(message, "sender_id", None)
        is_out = bool(getattr(message, "out", False))
        if not is_out and (bot_id is None or sender_id != bot_id):
            return False
        return mh.media_kind(message) in EDITABLE_MEDIA_KINDS

    def _other_admin_keys_on_messages(
        self, messages: Sequence[Any], *, current_msg_id: int, actor_id: int
    ) -> set[str]:
        """Collect reaction keys held by other admins or on sibling messages of the same Album."""

        held: set[str] = set()
        for msg in messages:
            reactions = getattr(msg, "reactions", None)
            recent = getattr(reactions, "recent_reactions", None) or []
            for item in recent:
                peer_id = getattr(item, "peer_id", None)
                if not isinstance(peer_id, types.PeerUser):
                    continue
                uid = int(peer_id.user_id)
                if uid not in self.boot.admin_ids:
                    continue
                if int(msg.id) == current_msg_id and uid == actor_id:
                    continue
                rkey = reaction_key(getattr(item, "reaction", None))
                if rkey:
                    held.add(rkey)
        return held

    async def _resolve_album_carrier(
        self, bot: TelegramClient, peer: Any, reacted_msg: Any
    ) -> tuple[Any, list[Any]]:
        """Return ``(caption_carrier_message, group_messages)``."""

        grouped_id = getattr(reacted_msg, "grouped_id", None)
        if not grouped_id:
            return reacted_msg, [reacted_msg]

        msg_id = int(reacted_msg.id)
        probe_ids = list(range(max(1, msg_id - 10), msg_id + 11))
        loaded = await self.limiter.call("read", bot.get_messages, peer, ids=probe_ids)
        if not isinstance(loaded, list):
            loaded = [loaded]
        siblings = sorted(
            [m for m in loaded if m is not None and getattr(m, "grouped_id", None) == grouped_id],
            key=lambda m: int(m.id),
        )
        if not siblings:
            return reacted_msg, [reacted_msg]
        for candidate in siblings:
            if mh.message_text(candidate):
                return candidate, siblings
        return siblings[0], siblings

    async def _on_raw_reaction(self, update: types.UpdateBotMessageReaction) -> None:
        try:
            await self.handle_reaction_update(update)
        except Exception:
            log.exception("Failed handling reaction update on message %s", getattr(update, "msg_id", "?"))

    async def handle_reaction_update(self, update: types.UpdateBotMessageReaction) -> bool:
        """Process a single ``UpdateBotMessageReaction``. Returns ``True`` if message text was edited."""

        if not self.store.current.tags.enabled:
            return False
        if isinstance(update.peer, types.PeerUser):
            return False
        if not isinstance(update.actor, types.PeerUser):
            return False
        actor_id = int(update.actor.user_id)
        if actor_id not in self.boot.admin_ids:
            return False

        bot = self.clients.bot
        if bot is None:
            return False

        rules = await self.repo.list_reaction_tags()
        if not rules:
            return False

        known_keys = {rule.emoji_key for rule in rules}
        old_keys = [k for k in (reaction_key(r) for r in (update.old_reactions or [])) if k]
        new_keys = [k for k in (reaction_key(r) for r in (update.new_reactions or [])) if k]
        if not any(k in known_keys for k in old_keys) and not any(k in known_keys for k in new_keys):
            return False

        peer_id_int = int(utils.get_peer_id(update.peer))
        peer_id_str = str(peer_id_int)
        candidate_ids = _candidate_peer_ids(update.peer)
        if not await self._is_configured_target_peer(bot, candidate_ids):
            return False

        msg_id = int(update.msg_id)
        bot_id = int(self.clients.bot_me.id) if self.clients.bot_me is not None else None

        # Serialize edits per message (and re-lock on album carrier if different).
        async with self._lock_for(peer_id_str, msg_id):
            reacted_msg = await self.limiter.call("read", bot.get_messages, update.peer, ids=msg_id)
            if isinstance(reacted_msg, list):
                reacted_msg = reacted_msg[0] if reacted_msg else None
            if not self._is_bot_authored_archive_message(reacted_msg, candidate_ids, bot_id):
                return False

            carrier, group_msgs = await self._resolve_album_carrier(bot, update.peer, reacted_msg)
            if int(carrier.id) != msg_id:
                async with self._lock_for(peer_id_str, int(carrier.id)):
                    # Re-fetch carrier inside carrier lock so rapid album taps never race.
                    fresh = await self.limiter.call("read", bot.get_messages, update.peer, ids=int(carrier.id))
                    if isinstance(fresh, list):
                        fresh = fresh[0] if fresh else None
                    if fresh is not None:
                        carrier = fresh
                    return await self._apply_edit_to_carrier(
                        bot, update.peer, carrier, group_msgs, rules, old_keys, new_keys, msg_id, actor_id, peer_id_int, candidate_ids, bot_id
                    )
            return await self._apply_edit_to_carrier(
                bot, update.peer, carrier, group_msgs, rules, old_keys, new_keys, msg_id, actor_id, peer_id_int, candidate_ids, bot_id
            )

    async def _apply_edit_to_carrier(
        self,
        bot: TelegramClient,
        peer: Any,
        carrier: Any,
        group_msgs: Sequence[Any],
        rules: Sequence[ReactionTagRule],
        old_keys: Sequence[str],
        new_keys: Sequence[str],
        reacted_msg_id: int,
        actor_id: int,
        peer_id_int: int,
        candidate_ids: set[str],
        bot_id: int | None,
    ) -> bool:
        if not self._is_bot_authored_archive_message(carrier, candidate_ids, bot_id):
            return False

        managed_tags = {rule.tag for rule in rules}
        current_text = mh.message_text(carrier)
        existing_tags, _, _ = split_managed_prefix(current_text, managed_tags)
        other_keys = self._other_admin_keys_on_messages(
            group_msgs, current_msg_id=reacted_msg_id, actor_id=actor_id
        )
        desired_tags = compute_desired_tags(
            existing_tags,
            rules,
            old_keys=old_keys,
            new_keys=new_keys,
            other_active_keys=other_keys,
        )
        new_text, new_entities = apply_prefix_tags(
            current_text, getattr(carrier, "entities", None), managed_tags, desired_tags
        )
        if new_text == current_text:
            return False

        has_webpage = isinstance(getattr(carrier, "media", None), types.MessageMediaWebPage)
        ignore_errors = CAPTION_TOO_LONG_ERRORS + PERMISSION_ERRORS + SOURCE_MISSING_ERRORS + BAD_REQUEST_ERRORS
        try:
            await self.limiter.call(
                "edit",
                bot.edit_message,
                peer,
                int(carrier.id),
                new_text,
                parse_mode=None,
                formatting_entities=new_entities,
                link_preview=has_webpage,
            )
            log.info("Updated reaction tags on %s#%s -> %s", peer_id_int, carrier.id, desired_tags or "(cleared)")
            return True
        except ignore_errors as exc:
            # MessageNotModifiedError, MediaCaptionTooLongError, MessageTooLongError,
            # or unsupported media types are silently ignored without altering the message.
            log.debug("Skipped reaction tag edit on %s#%s: %s", peer_id_int, carrier.id, exc)
            return False
