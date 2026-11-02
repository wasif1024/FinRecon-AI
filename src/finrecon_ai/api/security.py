"""Passwords, access tokens and login throttling.

Tokens carry identity only (user, tenant). Roles are loaded from the database on every request, so a role change
or deactivation takes effect immediately rather than when the token expires.
"""

import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

ALGORITHM = "HS256"
ISSUER = "finrecon-ai"
MIN_PASSWORD_LENGTH = 12
MIN_SECRET_LENGTH = 32

_hasher = PasswordHasher()
# Verified against when the user does not exist, so both paths take about the same time.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    expires_at: datetime


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_token(user_id: uuid.UUID, tenant_id: uuid.UUID, secret: str, ttl_minutes: int) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=ttl_minutes)
    claims = {"sub": str(user_id), "tid": str(tenant_id), "iat": now, "exp": expires, "iss": ISSUER,
              "jti": uuid.uuid4().hex}
    return jwt.encode(claims, secret, algorithm=ALGORITHM), expires


def decode_token(token: str, secret: str) -> TokenClaims:
    try:
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM], issuer=ISSUER,
                            options={"require": ["sub", "tid", "exp", "iat", "iss"]})
        return TokenClaims(uuid.UUID(claims["sub"]), uuid.UUID(claims["tid"]),
                           datetime.fromtimestamp(claims["exp"], UTC))
    except (jwt.PyJWTError, ValueError) as exc:
        raise InvalidToken(str(exc)) from None


class LoginThrottle:
    """Blocks an account after repeated failures. In-process only: run one API process, or move this to a
    shared store before scaling out."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 900):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key)) >= self.max_failures

    def failure(self, key: str) -> None:
        with self._lock:
            self._recent(key).append(time.monotonic())

    def success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def _recent(self, key: str) -> deque[float]:
        attempts = self._failures[key]
        while attempts and time.monotonic() - attempts[0] > self.window:
            attempts.popleft()
        return attempts
