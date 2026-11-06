"""Encryption of secrets stored in the database (e.g. WebDAV passwords).

The key comes from ``SECRET_KEY`` (a Fernet key) or, if absent, from
``<DATA_DIR>/secret.key`` which is generated once with 0600 permissions.
Losing the key only means stored WebDAV passwords must be re-entered.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:v1:"


def _normalise_key(raw: str) -> bytes:
    raw = raw.strip()
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        if len(decoded) == 32:
            return raw.encode("ascii")
    except (ValueError, UnicodeEncodeError):
        pass
    # Accept any passphrase by deriving a Fernet key from it.
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def load_or_create_key(secret_key: str, data_dir: Path) -> bytes:
    if secret_key:
        return _normalise_key(secret_key)
    path = data_dir / "secret.key"
    if path.exists():
        return _normalise_key(path.read_text(encoding="ascii"))
    data_dir.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(key.decode("ascii"))
    return key


class SecretBox:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        if value.startswith(PREFIX):
            return value
        return PREFIX + self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str | None) -> str | None:
        if value is None or value == "" or not value.startswith(PREFIX):
            return value
        try:
            return self._fernet.decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Stored secret cannot be decrypted; was SECRET_KEY changed?") from exc
