"""Logging setup with secret redaction."""

from __future__ import annotations

import logging
import re
from pathlib import Path

_REDACTIONS = (
    # Bot tokens: 123456789:AA...
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"), "<bot-token>"),
    # Credentials embedded in URLs: scheme://user:pass@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@", re.IGNORECASE), r"\1\2:***@"),
    # Authorization headers
    (re.compile(r"(Authorization:\s*)(\S+\s+)?\S+", re.IGNORECASE), r"\1***"),
)


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> logging.Logger:
    formatter = RedactingFormatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=getattr(logging, level, logging.INFO), handlers=handlers, force=True)
    # Telethon is chatty at INFO (connection notices); keep its warnings.
    logging.getLogger("telethon").setLevel(max(logging.WARNING, getattr(logging, level, logging.INFO)))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger("migrator")
