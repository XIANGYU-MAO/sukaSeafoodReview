import os
from dataclasses import dataclass
from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_network

from app.image_origins import (
    DEFAULT_IMAGE_ORIGIN_ALLOWLIST,
    ImageOriginError,
    normalize_image_origin_allowlist,
)


TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_ENV_VALUES = frozenset({"0", "false", "no", "off"})


def parse_boolean_setting(name: str, value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_ENV_VALUES:
        return True
    if normalized in FALSE_ENV_VALUES:
        return False
    raise ValueError(
        f"{name} must be one of: "
        + ", ".join(sorted(TRUE_ENV_VALUES | FALSE_ENV_VALUES))
    )


def normalize_trusted_proxy_network(value: str) -> IPv4Network | IPv6Network:
    network = ip_network(value, strict=False)
    if isinstance(network, IPv6Network) and network.prefixlen >= 96:
        mapped = network.network_address.ipv4_mapped
        if mapped is not None:
            return ip_network(
                f"{mapped}/{network.prefixlen - 96}", strict=False
            )
    return network


@dataclass(frozen=True)
class Settings:
    DATABASE_URL: str
    SESSION_COOKIE_NAME: str
    SESSION_HOURS: int
    SESSION_SECRET: str
    CSRF_SECRET: str
    APP_ENV: str
    RECEIPT_SECRET: str | None = None
    TRUSTED_PROXY_CIDRS: tuple[str, ...] = ()
    IMAGE_ORIGIN_ALLOWLIST: tuple[str, ...] = DEFAULT_IMAGE_ORIGIN_ALLOWLIST
    secure_cookie: bool = True
    app_name: str = "SukaSeafood Review API"

    def __post_init__(self) -> None:
        if self.APP_ENV.lower() == "production" and self.DATABASE_URL.startswith("sqlite"):
            raise ValueError("SQLite is not supported in production")
        if self.APP_ENV.lower() == "production" and not self.secure_cookie:
            raise ValueError("SECURE_COOKIE must be true in production")
        for value in self.TRUSTED_PROXY_CIDRS:
            try:
                network = normalize_trusted_proxy_network(value)
            except ValueError as exc:
                raise ValueError(
                    f"Trusted proxy entry must be an IP address or CIDR: {value!r}"
                ) from exc
            if network.prefixlen == 0:
                raise ValueError("Trusted proxy CIDR cannot cover the full network")
        try:
            normalized_origins = normalize_image_origin_allowlist(
                (*DEFAULT_IMAGE_ORIGIN_ALLOWLIST, *self.IMAGE_ORIGIN_ALLOWLIST)
            )
        except ImageOriginError as exc:
            raise ValueError(str(exc)) from exc
        object.__setattr__(self, "IMAGE_ORIGIN_ALLOWLIST", normalized_origins)

    def validate_api_secrets(self) -> None:
        if self.APP_ENV.lower() != "production":
            return
        receipt_secret = getattr(self, "RECEIPT_SECRET", None)
        if (
            receipt_secret is None
            or len(receipt_secret) < 32
            or len(set(receipt_secret)) < 8
        ):
            raise ValueError("RECEIPT_SECRET must be a strong independent secret")
        if receipt_secret in {self.SESSION_SECRET, self.CSRF_SECRET}:
            raise ValueError("RECEIPT_SECRET must be independent from other secrets")

    @classmethod
    def from_env(cls) -> "Settings":
        required = (
            "DATABASE_URL",
            "SESSION_COOKIE_NAME",
            "SESSION_HOURS",
            "SESSION_SECRET",
            "CSRF_SECRET",
            "APP_ENV",
        )
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")
        return cls(
            DATABASE_URL=os.environ["DATABASE_URL"],
            SESSION_COOKIE_NAME=os.environ["SESSION_COOKIE_NAME"],
            SESSION_HOURS=int(os.environ["SESSION_HOURS"]),
            SESSION_SECRET=os.environ["SESSION_SECRET"],
            CSRF_SECRET=os.environ["CSRF_SECRET"],
            RECEIPT_SECRET=os.getenv("RECEIPT_SECRET"),
            APP_ENV=os.environ["APP_ENV"],
            TRUSTED_PROXY_CIDRS=tuple(
                value.strip()
                for value in os.getenv("TRUSTED_PROXY_CIDRS", "").split(",")
                if value.strip()
            ),
            IMAGE_ORIGIN_ALLOWLIST=tuple(
                value.strip()
                for value in os.getenv(
                    "IMAGE_ORIGIN_ALLOWLIST",
                    ",".join(DEFAULT_IMAGE_ORIGIN_ALLOWLIST),
                ).split(",")
                if value.strip()
            ),
            secure_cookie=parse_boolean_setting(
                "SECURE_COOKIE", os.getenv("SECURE_COOKIE"), default=True
            ),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
