import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    DATABASE_URL: str
    SESSION_COOKIE_NAME: str
    SESSION_HOURS: int
    SESSION_SECRET: str
    CSRF_SECRET: str
    APP_ENV: str
    app_name: str = "SukaSeafood Review API"

    def __post_init__(self) -> None:
        if self.APP_ENV.lower() == "production" and self.DATABASE_URL.startswith("sqlite"):
            raise ValueError("SQLite is not supported in production")

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
            APP_ENV=os.environ["APP_ENV"],
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
