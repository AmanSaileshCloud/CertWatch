"""Auth primitives: password hashing (stdlib PBKDF2) and JWT tokens (PyJWT).

Pure-Python, no native builds. Password hashes are stored as a self-describing
string: ``pbkdf2_sha256$<rounds>$<salt_b64>$<hash_b64>``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

PBKDF2_ROUNDS = 200_000
_ALGO = "HS256"


def hash_password(password: str, *, rounds: int = PBKDF2_ROUNDS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt = _unb64(salt_b64)
        expected = _unb64(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds_s))
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


def create_access_token(username: str, role: str, secret: str, expire_minutes: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    return jwt.encode(payload, secret, algorithm=_ALGO)


def decode_access_token(token: str, secret: str) -> dict:
    """Returns the claims, or raises jwt.PyJWTError (invalid/expired)."""
    return jwt.decode(token, secret, algorithms=[_ALGO])


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))
