"""Symmetric encryption for sensitive config values (e.g. LLM API keys).

Encryption key is read from LLM_ENCRYPT_KEY env var (Fernet URL-safe base64).
If not set, a key is auto-generated and stored in ConfigKV so it persists.

Encrypted values are prefixed with 'enc:' for migration safety.
Plain values without the prefix are returned as-is (backward compatibility).
"""
from __future__ import annotations

import os
import secrets
import base64
from typing import Optional

_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet

    from cryptography.fernet import Fernet

    raw_key = os.getenv("LLM_ENCRYPT_KEY", "")
    if raw_key:
        key = raw_key.encode()
    else:
        from ..db import SessionLocal
        from ..models import ConfigKV

        db = SessionLocal()
        try:
            row = db.query(ConfigKV).filter(ConfigKV.key == "llm_encrypt_key").one_or_none()
            if row and row.value:
                key = row.value.encode()
            else:
                key = Fernet.generate_key()
                entry = ConfigKV(key="llm_encrypt_key", value=key.decode())
                db.add(entry)
                db.commit()
        finally:
            db.close()

    _fernet = Fernet(key)
    return _fernet


def encrypt_value(plain: str) -> str:
    """Encrypt a string. Returns 'enc:<base64>' prefixed ciphertext."""
    if not plain:
        return plain
    token = _get_fernet().encrypt(plain.encode()).decode()
    return f"enc:{token}"


def decrypt_value(value: str) -> str:
    """Decrypt an encrypted value. Plain (non-prefixed) values are returned as-is."""
    if not value or not value.startswith("enc:"):
        return value
    token = value[4:].encode()
    return _get_fernet().decrypt(token).decode()
