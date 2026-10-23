"""Encryption at rest for API keys (P1-08, NFR-02)."""

import os
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.fernet_key
    if not key:
        path = settings.data_dir / ".secret_key"
        if path.exists():
            key = path.read_text().strip()
        else:
            key = Fernet.generate_key().decode()
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(key)
    return Fernet(key.encode())


def encrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None


def mask(value: Optional[str]) -> Optional[str]:
    """sk-or-v1-abc...wxyz -> 'sk-…wxyz'. Never returns the full key."""
    if not value:
        return None
    prefix = value[:3] if len(value) > 12 else ""
    return f"{prefix}…{value[-4:]}" if len(value) > 8 else "…"
