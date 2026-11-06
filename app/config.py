"""Configuration.

Two layers exist on purpose:

* :class:`BootstrapConfig` comes from environment variables / ``.env``.  It
  holds credentials and infrastructure settings that must be known before the
  database or any Telegram connection exists (API keys, admin IDs, database
  URL, proxy).  The proxy deliberately lives here: if it could be changed from
  the Bot, a bad value would cut the Bot off from Telegram with no way back.
* :class:`RuntimeSettings` is stored in the database and edited from the Bot
  (or imported from YAML).  It controls transfer behaviour.
"""

from __future__ import annotations

import copy
import os
import typing
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit


GIB = 1024 * 1024 * 1024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y", "开", "是"}:
        return True
    if text in {"0", "false", "no", "off", "n", "关", "否"}:
        return False
    raise ValueError(f"Not a boolean value: {value!r}")


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Bootstrap configuration (environment)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProxyConfig:
    """One optional proxy, applied per traffic class.

    Internal destinations (loopback, RFC1918, link-local, single-label Docker
    service names, ``.local``/``.lan``/``.internal`` names and ``NO_PROXY``
    entries) always bypass the proxy; see :mod:`app.proxy`.
    """

    enabled: bool = False
    scheme: str = "socks5"  # socks5 | http
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""
    rdns: bool = True
    apply_to_telegram: bool = True
    apply_to_webdav: bool = True
    no_proxy: tuple[str, ...] = ()
    bypass_resolve: bool = True

    @classmethod
    def from_env(cls, env: typing.Mapping[str, str]) -> "ProxyConfig":
        raw_url = (env.get("PROXY_URL") or "").strip()
        no_proxy = tuple(_split_csv(env.get("NO_PROXY") or env.get("no_proxy")))
        bypass_resolve = parse_bool(env.get("PROXY_BYPASS_RESOLVE"), True)
        if not raw_url:
            return cls(no_proxy=no_proxy, bypass_resolve=bypass_resolve)

        parsed = urlsplit(raw_url)
        scheme = parsed.scheme.lower()
        rdns = True
        if scheme in {"socks5", "socks5h"}:
            rdns = True
            scheme = "socks5"
        elif scheme == "http":
            pass
        else:
            raise ValueError("PROXY_URL must use socks5://, socks5h:// or http:// (HTTP CONNECT)")
        if not parsed.hostname:
            raise ValueError("PROXY_URL needs a hostname")
        port = parsed.port or (1080 if scheme == "socks5" else 8080)

        apply_to = {item.lower() for item in _split_csv(env.get("PROXY_APPLY_TO") or "telegram,webdav")}
        unknown = apply_to - {"telegram", "webdav"}
        if unknown:
            raise ValueError(f"PROXY_APPLY_TO has unknown entries: {', '.join(sorted(unknown))}")

        return cls(
            enabled=True,
            scheme=scheme,
            host=parsed.hostname,
            port=port,
            username=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            rdns=rdns,
            apply_to_telegram="telegram" in apply_to,
            apply_to_webdav="webdav" in apply_to,
            no_proxy=no_proxy,
            bypass_resolve=bypass_resolve,
        )

    def httpx_url(self) -> str:
        """Proxy URL for httpx; credentials are percent-encoded."""

        scheme = "socks5h" if self.scheme == "socks5" and self.rdns else self.scheme
        auth = ""
        if self.username:
            auth = quote(self.username, safe="")
            if self.password:
                auth += ":" + quote(self.password, safe="")
            auth += "@"
        return f"{scheme}://{auth}{self.host}:{self.port}"

    def telethon_proxy(self) -> dict[str, Any] | None:
        if not self.enabled or not self.apply_to_telegram:
            return None
        proxy: dict[str, Any] = {
            "proxy_type": self.scheme,
            "addr": self.host,
            "port": self.port,
            "rdns": self.rdns,
        }
        if self.username:
            proxy["username"] = self.username
        if self.password:
            proxy["password"] = self.password
        return proxy

    def describe(self) -> str:
        """Log-safe description (never contains credentials)."""

        if not self.enabled:
            return "disabled"
        targets = [name for name, on in (("telegram", self.apply_to_telegram), ("webdav", self.apply_to_webdav)) if on]
        return f"{self.scheme}://{self.host}:{self.port} for {','.join(targets) or 'nothing'}"


@dataclass(frozen=True)
class BootstrapConfig:
    api_id: int
    api_hash: str
    bot_token: str
    admin_ids: frozenset[int]
    notify_chat_ids: tuple[int, ...]
    database_url: str
    secret_key: str
    data_dir: Path
    sessions_dir: Path
    downloads_dir: Path
    archive_root: Path
    user_session_name: str
    bot_session_name: str
    use_bot_as_writer: bool
    log_level: str
    log_file: Path | None
    proxy: ProxyConfig

    @property
    def active_dir(self) -> Path:
        return self.downloads_dir / "active"

    @property
    def failed_dir(self) -> Path:
        return self.downloads_dir / "failed"

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.sessions_dir, self.downloads_dir, self.active_dir, self.failed_dir):
            path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.sessions_dir, 0o700)
        except OSError:
            pass
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)


def load_bootstrap(env: typing.Mapping[str, str] | None = None, *, require_bot: bool = True) -> BootstrapConfig:
    """Read the bootstrap configuration from the environment."""

    if env is None:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:  # pragma: no cover - dependency is pinned
            pass
        env = os.environ

    try:
        api_id = int(env.get("API_ID") or 0)
    except ValueError as exc:
        raise ValueError("API_ID must be an integer") from exc
    api_hash = (env.get("API_HASH") or "").strip()
    if api_id <= 0 or not api_hash:
        raise ValueError("API_ID and API_HASH are required (get them at https://my.telegram.org)")

    bot_token = (env.get("BOT_TOKEN") or "").strip()
    if require_bot and not bot_token:
        raise ValueError("BOT_TOKEN is required: the Bot is the control panel of this service")

    admin_ids: set[int] = set()
    for item in _split_csv(env.get("ADMIN_IDS")):
        try:
            admin_ids.add(int(item))
        except ValueError as exc:
            raise ValueError(f"ADMIN_IDS contains a non-numeric value: {item!r}") from exc
    if require_bot and not admin_ids:
        raise ValueError("ADMIN_IDS is required: list the numeric Telegram user IDs allowed to control the Bot")

    notify_chat_ids: list[int] = []
    for item in _split_csv(env.get("NOTIFY_CHAT_IDS")):
        notify_chat_ids.append(int(item))
    if not notify_chat_ids:
        notify_chat_ids = sorted(admin_ids)

    data_dir = Path(env.get("DATA_DIR") or "data").resolve()
    sessions_dir = Path(env.get("SESSIONS_DIR") or "sessions").resolve()
    downloads_dir = Path(env.get("DOWNLOADS_DIR") or "downloads").resolve()
    archive_root = Path(env.get("ARCHIVE_ROOT") or "archive").resolve()
    database_url = (env.get("DATABASE_URL") or "").strip() or f"sqlite+aiosqlite:///{data_dir / 'migrator.db'}"
    log_file_raw = (env.get("LOG_FILE") or "").strip()

    return BootstrapConfig(
        api_id=api_id,
        api_hash=api_hash,
        bot_token=bot_token,
        admin_ids=frozenset(admin_ids),
        notify_chat_ids=tuple(notify_chat_ids),
        database_url=database_url,
        secret_key=(env.get("SECRET_KEY") or "").strip(),
        data_dir=data_dir,
        sessions_dir=sessions_dir,
        downloads_dir=downloads_dir,
        archive_root=archive_root,
        user_session_name=(env.get("USER_SESSION_NAME") or "user").strip(),
        bot_session_name=(env.get("BOT_SESSION_NAME") or "bot").strip(),
        use_bot_as_writer=parse_bool(env.get("USE_BOT_AS_WRITER"), True),
        log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper(),
        log_file=Path(log_file_raw).resolve() if log_file_raw else None,
        proxy=ProxyConfig.from_env(env),
    )


# ---------------------------------------------------------------------------
# Runtime settings (database, editable from the Bot)
# ---------------------------------------------------------------------------


@dataclass
class IncludeSettings:
    """Which message kinds are transferred."""

    text: bool = True
    photo: bool = True
    video: bool = True
    animation: bool = True
    audio: bool = True
    voice: bool = True
    video_note: bool = True
    document: bool = False
    sticker: bool = False
    # Polls, contacts, locations, dice, games...: can only be copied natively.
    other: bool = False


@dataclass
class TransferSettings:
    include: IncludeSettings = field(default_factory=IncludeSettings)
    # Native copy hides the original author (MTProto drop_author); false keeps
    # the "Forwarded from" header when Telegram allows forwarding.
    hide_sender: bool = True
    drop_caption: bool = False
    # Remove Telegram spoiler masks (forces a re-upload instead of a native copy).
    strip_spoilers: bool = True
    # Try a server-side copy/forward first (only when the reading account writes).
    native_copy: bool = True
    # Skip instead of downloading when a native copy is impossible.
    native_only: bool = False
    # 0 = automatic per sending account (2000 MiB, 4000 MiB for Premium users).
    max_upload_bytes: int = 0
    allow_download_unknown_size: bool = False
    # Let a Premium reading account send 2-4 GiB files the Bot cannot send.
    allow_premium_user_fallback: bool = False
    # What to do with a caption longer than the writer accepts: split | truncate
    caption_overflow: str = "split"


@dataclass
class ArchiveSettings:
    # Sidecar for text messages and captions: json | md | both | none
    sidecar_format: str = "json"
    # Also write a sidecar for media without caption.
    sidecar_always: bool = False
    # Insert a YYYY-MM directory level.
    group_by_month: bool = False
    # Source folder name: title_id | id | title
    folder_naming: str = "title_id"
    # WebDAV transfer mode: auto (spool when disk allows, else stream) | stream | spool
    webdav_mode: str = "auto"


@dataclass
class FilterSettings:
    """Global switches; individual rules live in the filter_rules table."""

    enabled: bool = False
    case_sensitive: bool = False


@dataclass
class QueueSettings:
    max_attempts: int = 4
    retry_backoff_seconds: list[int] = field(default_factory=lambda: [300, 600, 1800])
    record_skipped: bool = True


@dataclass
class LimitSettings:
    global_min_delay_seconds: float = 1.0
    resolve_delay_seconds: float = 2.0
    read_delay_seconds: float = 2.0
    download_delay_seconds: float = 5.0
    copy_delay_seconds: float = 10.0
    upload_delay_seconds: float = 30.0
    verify_delay_seconds: float = 2.0
    floodwait_extra_min_seconds: int = 5
    floodwait_extra_max_seconds: int = 20
    floodwait_defer_after_seconds: int = 60
    account_restricted_retry_seconds: int = 86_400

    def delay_for(self, operation: str) -> float:
        return {
            "resolve": self.resolve_delay_seconds,
            "read": self.read_delay_seconds,
            "download": self.download_delay_seconds,
            "copy": self.copy_delay_seconds,
            "upload": self.upload_delay_seconds,
            "verify": self.verify_delay_seconds,
        }.get(operation, self.global_min_delay_seconds)


@dataclass
class WorkerSettings:
    batch_size: int = 25
    pause_between_batches_seconds: int = 600
    idle_sleep_seconds: int = 30
    concurrency: int = 1


@dataclass
class WatchSettings:
    # Periodic catch-up from the stored watermark (covers missed updates).
    poll_interval_seconds: int = 300
    # Upper bound for one catch-up pass per route.
    catchup_max_messages: int = 5000


@dataclass
class DownloadSettings:
    min_free_bytes: int = 5 * GIB
    max_job_bytes: int = 0
    keep_failed: bool = False
    max_failed_bytes: int = 0


@dataclass
class RuntimeSettings:
    transfer: TransferSettings = field(default_factory=TransferSettings)
    archive: ArchiveSettings = field(default_factory=ArchiveSettings)
    filters: FilterSettings = field(default_factory=FilterSettings)
    queue: QueueSettings = field(default_factory=QueueSettings)
    limits: LimitSettings = field(default_factory=LimitSettings)
    worker: WorkerSettings = field(default_factory=WorkerSettings)
    watch: WatchSettings = field(default_factory=WatchSettings)
    downloads: DownloadSettings = field(default_factory=DownloadSettings)


SECTION_NAMES = tuple(item.name for item in fields(RuntimeSettings))

CHOICES: dict[str, tuple[str, ...]] = {
    "transfer.caption_overflow": ("split", "truncate"),
    "archive.sidecar_format": ("json", "md", "both", "none"),
    "archive.folder_naming": ("title_id", "id", "title"),
    "archive.webdav_mode": ("auto", "stream", "spool"),
}

MINIMUMS: dict[str, float] = {
    "queue.max_attempts": 1,
    "worker.batch_size": 1,
    "worker.concurrency": 1,
    "worker.idle_sleep_seconds": 1,
    "watch.poll_interval_seconds": 30,
    "limits.floodwait_defer_after_seconds": 1,
    "limits.account_restricted_retry_seconds": 1,
}

MAXIMUMS: dict[str, float] = {
    "worker.concurrency": 8,
}


def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def _coerce(value: Any, annotation: Any, path: str) -> Any:
    origin = typing.get_origin(annotation)
    if annotation is bool:
        return parse_bool(value)
    if annotation is int:
        if isinstance(value, bool):
            raise ValueError(f"{path} must be an integer")
        result = int(str(value).strip()) if isinstance(value, str) else int(value)
        return result
    if annotation is float:
        if isinstance(value, bool):
            raise ValueError(f"{path} must be a number")
        return float(value)
    if annotation is str:
        return str(value).strip()
    if origin is list:
        (item_type,) = typing.get_args(annotation) or (str,)
        if isinstance(value, str):
            value = [item for item in value.replace(";", ",").split(",") if item.strip()]
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{path} must be a list")
        return [_coerce(item, item_type, path) for item in value]
    raise TypeError(f"Unsupported settings type for {path}: {annotation!r}")


def _validate_leaf(path: str, value: Any) -> None:
    if path in CHOICES and value not in CHOICES[path]:
        raise ValueError(f"{path} must be one of: {', '.join(CHOICES[path])}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = MINIMUMS.get(path, 0)
        if value < minimum:
            raise ValueError(f"{path} must be >= {minimum:g}")
        maximum = MAXIMUMS.get(path)
        if maximum is not None and value > maximum:
            raise ValueError(f"{path} must be <= {maximum:g}")
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (int, float)) and item < 0:
                raise ValueError(f"{path} cannot contain negative values")


def dataclass_from_dict(cls: type, data: Any, prefix: str = "", *, strict: bool = False) -> Any:
    """Build a settings dataclass from a (possibly partial) mapping.

    Missing keys use defaults.  Unknown keys are ignored unless ``strict``.
    """

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"{prefix or 'settings'} must be a mapping")
    hints = _hints(cls)
    known = {item.name for item in fields(cls)}
    if strict:
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown setting(s) under {prefix or 'root'}: {', '.join(sorted(unknown))}")
    kwargs: dict[str, Any] = {}
    for item in fields(cls):
        if item.name not in data:
            continue
        path = f"{prefix}.{item.name}" if prefix else item.name
        annotation = hints[item.name]
        raw = data[item.name]
        if is_dataclass(annotation):
            kwargs[item.name] = dataclass_from_dict(annotation, raw, path, strict=strict)
        else:
            value = _coerce(raw, annotation, path)
            _validate_leaf(path, value)
            kwargs[item.name] = value
    return cls(**kwargs)


def dataclass_to_dict(obj: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in fields(obj):
        value = getattr(obj, item.name)
        result[item.name] = dataclass_to_dict(value) if is_dataclass(value) else copy.deepcopy(value)
    return result


def settings_from_sections(sections: dict[str, Any], *, strict: bool = False) -> RuntimeSettings:
    return dataclass_from_dict(RuntimeSettings, sections, strict=strict)


def get_setting(settings: RuntimeSettings, path: str) -> Any:
    current: Any = settings
    for part in path.split("."):
        if not is_dataclass(current) or part not in {item.name for item in fields(current)}:
            raise KeyError(f"Unknown setting: {path}")
        current = getattr(current, part)
    return current


def set_setting(settings: RuntimeSettings, path: str, raw_value: Any) -> RuntimeSettings:
    """Return a copy of ``settings`` with one leaf value changed and validated."""

    parts = path.split(".")
    if len(parts) < 2:
        raise KeyError("Use a full dotted setting path, e.g. transfer.hide_sender")
    updated = copy.deepcopy(settings)
    parent: Any = updated
    for part in parts[:-1]:
        if not is_dataclass(parent) or part not in {item.name for item in fields(parent)}:
            raise KeyError(f"Unknown setting: {path}")
        parent = getattr(parent, part)
    leaf = parts[-1]
    if not is_dataclass(parent) or leaf not in {item.name for item in fields(parent)}:
        raise KeyError(f"Unknown setting: {path}")
    annotation = _hints(type(parent))[leaf]
    if is_dataclass(annotation):
        raise KeyError(f"{path} is a section; set one of its fields instead")
    value = _coerce(raw_value, annotation, path)
    _validate_leaf(path, value)
    setattr(parent, leaf, value)
    return updated


def leaf_paths(obj: Any = None, prefix: str = "") -> list[str]:
    obj = obj if obj is not None else RuntimeSettings()
    result: list[str] = []
    for item in fields(obj):
        value = getattr(obj, item.name)
        path = f"{prefix}.{item.name}" if prefix else item.name
        if is_dataclass(value):
            result.extend(leaf_paths(value, path))
        else:
            result.append(path)
    return result
