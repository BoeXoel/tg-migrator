"""Parse Telegram message links into (chat, topic, message range).

Supported forms::

    https://t.me/username/123
    https://t.me/username/456/123            (public forum topic 456)
    https://t.me/s/username/123
    https://t.me/c/1234567890/123            (private chat -> -1001234567890)
    https://t.me/c/1234567890/456/123        (private forum topic 456)
    ...?thread=456 / ?topic=456
    tg://resolve?domain=username&post=123[&thread=456]
    tg://privatepost?channel=1234567890&post=123[&thread=456]

Ranges::

    <link>-500   <link> - 500   <link> 500   <link> <link-to-end>
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

_LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/[^\s]+|tg://[^\s]+", re.IGNORECASE)
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_RESERVED = {"joinchat", "addstickers", "addemoji", "share", "proxy", "socks", "login", "setlanguage", "iv", "boost"}


@dataclass(frozen=True)
class ParsedLink:
    chat: str  # "@username" or "-100…" numeric id
    message_id: int
    topic_id: int | None = None
    end_message_id: int | None = None

    @property
    def start(self) -> int:
        return min(self.message_id, self.end_message_id or self.message_id)

    @property
    def end(self) -> int:
        return max(self.message_id, self.end_message_id or self.message_id)

    @property
    def is_range(self) -> bool:
        return self.end_message_id is not None and self.end_message_id != self.message_id

    @property
    def count(self) -> int:
        return self.end - self.start + 1


def private_chat_id(internal_id: str | int) -> str:
    return f"-100{int(internal_id)}"


def _int(value: str | None) -> int | None:
    if value is None or not str(value).isdigit():
        return None
    return int(value)


def parse_link(link: str) -> ParsedLink | None:
    link = link.strip().rstrip(".,;)")
    if link.lower().startswith("tg://"):
        return _parse_tg_scheme(link)
    if not re.match(r"^https?://", link, re.IGNORECASE):
        link = "https://" + link
    parsed = urlsplit(link)
    host = (parsed.hostname or "").lower()
    if host not in {"t.me", "telegram.me", "telegram.dog", "www.t.me"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)
    query_topic = _int((query.get("thread") or query.get("topic") or [None])[0])

    if parts and parts[0] == "s":
        parts = parts[1:]
    if not parts:
        return None

    if parts[0] == "c":
        # /c/<internal>/<msg>  or  /c/<internal>/<topic>/<msg>
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            return ParsedLink(private_chat_id(parts[1]), int(parts[2]), query_topic)
        if len(parts) == 4 and all(part.isdigit() for part in parts[1:]):
            return ParsedLink(private_chat_id(parts[1]), int(parts[3]), int(parts[2]))
        return None

    username = parts[0]
    if username.lower() in _RESERVED or username.startswith("+") or not _USERNAME_RE.match(username):
        return None
    if len(parts) == 2 and parts[1].isdigit():
        return ParsedLink(f"@{username}", int(parts[1]), query_topic)
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return ParsedLink(f"@{username}", int(parts[2]), int(parts[1]))
    return None


def _parse_tg_scheme(link: str) -> ParsedLink | None:
    parsed = urlsplit(link)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
    action = (parsed.netloc or parsed.path).lower().strip("/")
    post = _int(query.get("post"))
    topic = _int(query.get("thread") or query.get("topic"))
    if post is None:
        return None
    if action == "resolve" and query.get("domain") and _USERNAME_RE.match(query["domain"]):
        return ParsedLink(f"@{query['domain']}", post, topic)
    if action == "privatepost" and _int(query.get("channel")) is not None:
        return ParsedLink(private_chat_id(query["channel"]), post, topic)
    return None


def find_links(text: str) -> list[str]:
    return _LINK_RE.findall(text or "")


def parse_request(text: str) -> ParsedLink | None:
    """Parse a free-form Bot message into one link or one same-chat range."""

    text = (text or "").strip()
    raw_links = find_links(text)
    if not raw_links:
        return None
    first = _parse_with_inline_range(raw_links[0])
    if first is None:
        return None
    if first.end_message_id is not None:
        return first

    if len(raw_links) >= 2:
        second = parse_link(raw_links[1])
        if second is None or second.chat.lower() != first.chat.lower():
            raise ValueError("A range needs two links from the same chat")
        if (second.topic_id or None) != (first.topic_id or None):
            raise ValueError("A range needs two links from the same topic")
        return ParsedLink(first.chat, first.message_id, first.topic_id, second.message_id)

    # "<link> - 500" / "<link> 500" / "<link> to 500"
    tail = text.split(raw_links[0], 1)[1]
    match = re.match(r"^\s*(?:-|–|—|~|to|至|到)?\s*(\d+)\b", tail, re.IGNORECASE)
    if match:
        return ParsedLink(first.chat, first.message_id, first.topic_id, int(match.group(1)))
    return first


def _parse_with_inline_range(raw: str) -> ParsedLink | None:
    match = re.match(r"^(.*?/)(\d+)\s*[-–—~](\d+)(\?.*)?$", raw)
    if match:
        base = parse_link(f"{match.group(1)}{match.group(2)}{match.group(4) or ''}")
        if base is not None:
            return ParsedLink(base.chat, base.message_id, base.topic_id, int(match.group(3)))
    return parse_link(raw)
