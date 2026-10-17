"""Room secrets (MCP server tokens, room AI provider keys), stored encrypted with ROOM_SECRETS_KEY."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from mux.config import settings


logger = logging.getLogger(__name__)

# Where ensure_secrets_key writes a new key: the .env the settings read (relative to where the server starts)
ENV_FILE = Path(".env")
_EMPTY_LINE = re.compile(r"^ROOM_SECRETS_KEY=[ \t]*$", re.MULTILINE)


class SecretsUnavailable(RuntimeError):
    pass


def ensure_secrets_key() -> None:
    """With no ROOM_SECRETS_KEY (or old MCP_ENCRYPTION_KEY), make one and save it in .env, so room owners can
    save keys without setting it up by hand. The key must stay: losing it makes every saved key unreadable."""
    if settings.room_secrets_key or settings.mcp_encryption_key:
        return
    key = Fernet.generate_key().decode()
    try:
        text = ENV_FILE.read_text() if ENV_FILE.exists() else ""
        if _EMPTY_LINE.search(text):
            text = _EMPTY_LINE.sub(f"ROOM_SECRETS_KEY={key}", text, count=1)
        else:
            text += ("" if not text or text.endswith("\n") else "\n") + f"ROOM_SECRETS_KEY={key}\n"
        ENV_FILE.write_text(text)
    except OSError as e:
        logger.warning("No ROOM_SECRETS_KEY and %s can't be written (%s): rooms can't save keys", ENV_FILE, e)
        return
    settings.room_secrets_key = key
    logger.warning("Made a new ROOM_SECRETS_KEY in %s. Keep it (and copy it to deployed servers): "
                   "saved room keys can't be read without it", ENV_FILE.resolve())


def _fernet() -> MultiFernet:
    """Encrypts with ROOM_SECRETS_KEY (else the old MCP_ENCRYPTION_KEY) and decrypts with either, so values
    saved under the old key still read after the new one is set."""
    keys = [k for k in (settings.room_secrets_key, settings.mcp_encryption_key) if k]
    if not keys:
        raise SecretsUnavailable("Set ROOM_SECRETS_KEY on the server to save keys and tokens")
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except ValueError as e:
        raise SecretsUnavailable("ROOM_SECRETS_KEY isn't a valid Fernet key") from e


def encrypt_value(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_value(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as e:
        raise SecretsUnavailable("Saved keys can't be read with the current ROOM_SECRETS_KEY") from e


def encrypt_values(values: dict[str, str]) -> dict[str, str]:
    return {name: encrypt_value(value) for name, value in values.items()} if values else {}


def decrypt_values(values: dict[str, str]) -> dict[str, str]:
    return {name: decrypt_value(value) for name, value in values.items()} if values else {}
