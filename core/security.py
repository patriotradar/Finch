"""Security primitives for the private Aegis owner interface.

The module deliberately has no framework dependency so authentication rules can
be tested independently and reused by HTTP and WebSocket handlers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque


SESSION_COOKIE = "aegis_owner_session"
SESSION_LIFETIME_SECONDS = 8 * 60 * 60


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str, *, salt: bytes | None = None, iterations: int = 310_000) -> str:
    """Return a portable PBKDF2-SHA256 password hash."""
    if not password:
        raise ValueError("password_required")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        salt = _b64decode(raw_salt)
        expected = _b64decode(raw_digest)
    except (TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


@dataclass(frozen=True)
class Session:
    owner: str
    issued_at: int
    expires_at: int
    csrf_token: str


class OwnerAuth:
    """Issue and validate signed, expiring owner sessions."""

    def __init__(
        self,
        *,
        password_hash: str | None = None,
        legacy_password: str | None = None,
        session_secret: str | None = None,
        lifetime_seconds: int = SESSION_LIFETIME_SECONDS,
    ):
        self.password_hash = (password_hash or "").strip()
        self.legacy_password = legacy_password or ""
        self.session_secret = (session_secret or "").encode("utf-8")
        self.lifetime_seconds = lifetime_seconds

    @classmethod
    def from_environment(cls) -> "OwnerAuth":
        return cls(
            password_hash=os.environ.get("FINCH_ADMIN_PASSWORD_HASH"),
            legacy_password=os.environ.get("FINCH_ADMIN_PASSWORD"),
            session_secret=os.environ.get("FINCH_SESSION_SECRET"),
        )

    @property
    def configured(self) -> bool:
        return bool((self.password_hash or self.legacy_password) and self.session_secret)

    def check_password(self, candidate: str) -> bool:
        if not candidate:
            return False
        if self.password_hash:
            return verify_password(candidate, self.password_hash)
        return bool(self.legacy_password) and hmac.compare_digest(candidate, self.legacy_password)

    def issue(self, *, now: int | None = None) -> tuple[str, Session]:
        if not self.configured:
            raise RuntimeError("owner_auth_not_configured")
        issued_at = int(now if now is not None else time.time())
        session = Session(
            owner="owner",
            issued_at=issued_at,
            expires_at=issued_at + self.lifetime_seconds,
            csrf_token=secrets.token_urlsafe(24),
        )
        payload = _b64encode(json.dumps(session.__dict__, separators=(",", ":")).encode("utf-8"))
        signature = _b64encode(hmac.new(self.session_secret, payload.encode("ascii"), hashlib.sha256).digest())
        return f"{payload}.{signature}", session

    def validate(self, token: str | None, *, now: int | None = None) -> Session | None:
        if not token or not self.configured:
            return None
        try:
            payload, supplied_signature = token.split(".", 1)
            expected_signature = _b64encode(
                hmac.new(self.session_secret, payload.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(supplied_signature, expected_signature):
                return None
            data = json.loads(_b64decode(payload))
            session = Session(
                owner=str(data["owner"]),
                issued_at=int(data["issued_at"]),
                expires_at=int(data["expires_at"]),
                csrf_token=str(data["csrf_token"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        current = int(now if now is not None else time.time())
        if session.owner != "owner" or session.issued_at > current + 60 or session.expires_at <= current:
            return None
        return session


class LoginThrottle:
    """Small in-memory fail-closed limiter for owner login attempts."""

    def __init__(self, *, attempts: int = 5, window_seconds: int = 15 * 60):
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> Deque[float]:
        failures = self._failures[key]
        threshold = now - self.window_seconds
        while failures and failures[0] <= threshold:
            failures.popleft()
        return failures

    def allowed(self, key: str, *, now: float | None = None) -> bool:
        current = now if now is not None else time.time()
        with self._lock:
            return len(self._prune(key, current)) < self.attempts

    def failure(self, key: str, *, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        with self._lock:
            self._prune(key, current).append(current)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
