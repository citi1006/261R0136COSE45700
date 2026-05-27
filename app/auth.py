from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from app.config import AUTH_TOKEN_SECRET, AUTH_TOKEN_TTL_SECONDS


PASSWORD_HASH_SCHEME = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 210_000
MIN_PASSWORD_LENGTH = 8


def normalize_user_id(user_id: str) -> str:
    normalized = user_id.strip()
    if not normalized:
        raise ValueError("user_id is required")
    if len(normalized) > 80:
        raise ValueError("user_id must be 80 characters or fewer")
    return normalized


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def hash_password(password: str, *, salt: bytes | None = None, iterations: int = PASSWORD_HASH_ITERATIONS) -> str:
    validate_password(password)
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), actual_salt, iterations)
    return f"{PASSWORD_HASH_SCHEME}${iterations}${b64url_encode(actual_salt)}${b64url_encode(digest)}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations_text, salt_text, digest_text = stored_hash.split("$", 3)
        if scheme != PASSWORD_HASH_SCHEME:
            return False
        iterations = int(iterations_text)
        salt = b64url_decode(salt_text)
        expected = b64url_decode(digest_text)
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def sign_payload(payload_text: str, secret: str = AUTH_TOKEN_SECRET) -> str:
    signature = hmac.new(secret.encode("utf-8"), payload_text.encode("ascii"), hashlib.sha256).digest()
    return b64url_encode(signature)


def create_access_token(user_id: str, *, ttl_seconds: int = AUTH_TOKEN_TTL_SECONDS) -> str:
    payload: dict[str, Any] = {
        "sub": user_id,
        "exp": int(time.time()) + ttl_seconds,
    }
    payload_text = b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_text}.{sign_payload(payload_text)}"


def parse_access_token(token: str) -> str:
    try:
        payload_text, signature = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("invalid token") from exc

    expected_signature = sign_payload(payload_text)
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("invalid token signature")

    try:
        payload = json.loads(b64url_decode(payload_text).decode("utf-8"))
        user_id = str(payload["sub"])
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid token payload") from exc

    if expires_at < int(time.time()):
        raise ValueError("token expired")
    return normalize_user_id(user_id)
