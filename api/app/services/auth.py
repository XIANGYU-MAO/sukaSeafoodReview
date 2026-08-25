from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


FIXED_USERS = (
    ("Hassan", "reviewer"),
    ("Mao", "admin"),
    ("Xinhui", "reviewer"),
    ("Wahid", "reviewer"),
    ("Sharmaa", "reviewer"),
    ("Yiming", "reviewer"),
)
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_MINUTES = 5

_PASSWORD_HASHER = PasswordHasher(type=Type.ID)
_DUMMY_PASSWORD_HASH = _PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(encoded, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError, TypeError):
        return False


def verify_dummy_password(password: str) -> None:
    verify_password(password, _DUMMY_PASSWORD_HASH)


def session_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def generate_temporary_password() -> str:
    return secrets.token_urlsafe(24)


def csrf_token(token_hash: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), token_hash.encode("ascii"), hashlib.sha256
    ).hexdigest()


def verify_csrf_token(token_hash: str, secret: str, supplied: str) -> bool:
    return hmac.compare_digest(csrf_token(token_hash, secret), supplied)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class FailureState:
    count: int = 0
    locked_until: datetime | None = None


class LoginLimiter:
    """Per-application client-address limiter; account state remains in the DB."""

    def __init__(self) -> None:
        self._clients: dict[str, FailureState] = {}

    def is_limited(self, client_address: str, now: datetime) -> bool:
        state = self._clients.get(client_address)
        if state is None or state.locked_until is None:
            return False
        if as_utc(state.locked_until) <= now:
            self._clients.pop(client_address, None)
            return False
        return True

    def record_failure(self, client_address: str, now: datetime) -> bool:
        state = self._clients.setdefault(client_address, FailureState())
        state.count += 1
        if state.count >= LOGIN_FAILURE_LIMIT:
            state.locked_until = now + timedelta(minutes=LOGIN_LOCK_MINUTES)
            return True
        return False

    def clear(self, client_address: str) -> None:
        self._clients.pop(client_address, None)
