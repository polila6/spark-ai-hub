"""Password hashing, opaque tokens, API keys and at-rest encryption.

No custom cryptography: Argon2id (argon2-cffi) for passwords, `secrets` for tokens,
SHA-256 for token-at-rest lookup, Fernet (cryptography) for encrypted settings.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

_ph = PasswordHasher()  # argon2id with library defaults (RFC 9106 low-memory profile)

API_KEY_PREFIX = "sah-"
MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        # Constant-ish time even for users without password.
        _ph.hash(password)
        return False
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"min_length_{MIN_PASSWORD_LENGTH}")
    if password.lower() == password or password.upper() == password:
        problems.append("mixed_case")
    if not any(c.isdigit() for c in password):
        problems.append("digit")
    return problems


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_digest(token: str) -> str:
    """Server-side keyed digest of an opaque token (sessions, invitations, API keys)."""
    key = (get_settings().secret("session_secret") or "dev-only-secret").encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def new_api_key() -> tuple[str, str, str]:
    """Return (full_key, display_prefix, digest). Only the digest is stored."""
    raw = secrets.token_urlsafe(32)
    full = f"{API_KEY_PREFIX}{raw}"
    return full, full[:12], token_digest(full)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _fernet() -> Fernet:
    key = get_settings().secret("encryption_key")
    if not key:
        raise RuntimeError("Encryption key not configured (SAH_ENCRYPTION_KEY_FILE)")
    return Fernet(key.encode())


def encrypt_str(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_str(value: str) -> str | None:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return None
