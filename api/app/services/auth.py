from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
)
import secrets
from uuid import UUID

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import Select, select

from app.config import normalize_trusted_proxy_network
from app.models import User


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
LOGIN_CLIENT_WINDOW = timedelta(minutes=5)
LOGIN_LIMITER_MAX_CLIENTS = 10_000
MAX_FORWARDED_HOPS = 20
MAX_FORWARDED_LENGTH = 2048
NEW_PASSWORD_MIN_LENGTH = 12
NEW_PASSWORD_MAX_LENGTH = 128

_PASSWORD_HASHER = PasswordHasher(type=Type.ID)
_DUMMY_PASSWORD_HASH = _PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


class PasswordPolicyError(ValueError):
    pass


def require_valid_new_password(password: str) -> str:
    if not NEW_PASSWORD_MIN_LENGTH <= len(password) <= NEW_PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError(
            "New password must contain between 12 and 128 characters"
        )
    return password


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


def parse_trusted_proxy_networks(
    values: tuple[str, ...],
) -> tuple[IPv4Network | IPv6Network, ...]:
    return tuple(normalize_trusted_proxy_network(value) for value in values)


def normalize_ip_address(
    value: str, *, allow_scope: bool
) -> IPv4Address | IPv6Address:
    if not allow_scope and "%" in value:
        raise ValueError("Scoped IPv6 is not valid in a forwarded address")
    address = ip_address(value)
    if isinstance(address, IPv6Address):
        if address.scope_id is not None:
            if not allow_scope:
                raise ValueError("Scoped IPv6 is not valid in a forwarded address")
            address = ip_address(address.packed)
        mapped = address.ipv4_mapped
        if mapped is not None:
            return mapped
    return address


def resolve_client_address(
    peer_address: str,
    forwarded_for: str | None,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
) -> str:
    try:
        peer = normalize_ip_address(peer_address, allow_scope=True)
    except ValueError:
        return peer_address
    if not any(peer in network for network in trusted_proxy_networks):
        return peer.compressed
    if not forwarded_for or len(forwarded_for) > MAX_FORWARDED_LENGTH:
        return peer.compressed

    raw_hops = forwarded_for.split(",")
    if len(raw_hops) > MAX_FORWARDED_HOPS:
        return peer.compressed
    try:
        hops = [
            normalize_ip_address(value.strip(), allow_scope=False)
            for value in raw_hops
            if value.strip()
        ]
    except ValueError:
        return peer.compressed
    if len(hops) != len(raw_hops):
        return peer.compressed

    current = peer
    for hop in reversed(hops):
        if not any(current in network for network in trusted_proxy_networks):
            break
        current = hop
    return current.compressed


def user_by_name_for_update(name: str) -> Select[tuple[User]]:
    return (
        select(User)
        .where(User.name == name)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def user_by_id_for_update(user_id: UUID) -> Select[tuple[User]]:
    return (
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


@dataclass
class FailureState:
    count: int = 0
    locked_until: datetime | None = None
    last_failure: datetime | None = None


class LoginLimiter:
    """Per-application client-address limiter; account state remains in the DB."""

    def __init__(
        self,
        *,
        window: timedelta = LOGIN_CLIENT_WINDOW,
        max_entries: int = LOGIN_LIMITER_MAX_CLIENTS,
    ) -> None:
        if window <= timedelta(0):
            raise ValueError("Login limiter window must be positive")
        if max_entries < 1:
            raise ValueError("Login limiter capacity must be positive")
        self._window = window
        self._max_entries = max_entries
        self._clients: OrderedDict[str, FailureState] = OrderedDict()

    def _prune(self, now: datetime) -> None:
        while self._clients:
            address, state = next(iter(self._clients.items()))
            lock_active = bool(
                state.locked_until is not None
                and as_utc(state.locked_until) > now
            )
            if lock_active:
                break
            if (
                state.last_failure is not None
                and now - as_utc(state.last_failure) < self._window
            ):
                break
            self._clients.pop(address, None)

    def is_limited(self, client_address: str, now: datetime) -> bool:
        self._prune(now)
        state = self._clients.get(client_address)
        if state is None or state.locked_until is None:
            return False
        if as_utc(state.locked_until) <= now:
            self._clients.pop(client_address, None)
            return False
        return True

    def record_failure(self, client_address: str, now: datetime) -> bool:
        self._prune(now)
        state = self._clients.pop(client_address, None)
        if state is None:
            while len(self._clients) >= self._max_entries:
                self._clients.popitem(last=False)
            state = FailureState()
        state.count += 1
        state.last_failure = now
        self._clients[client_address] = state
        if state.count >= LOGIN_FAILURE_LIMIT:
            state.locked_until = now + timedelta(minutes=LOGIN_LOCK_MINUTES)
            return True
        return False

    def clear(self, client_address: str) -> None:
        self._clients.pop(client_address, None)

    @property
    def tracked_count(self) -> int:
        return len(self._clients)

    def is_tracked(self, client_address: str) -> bool:
        return client_address in self._clients
