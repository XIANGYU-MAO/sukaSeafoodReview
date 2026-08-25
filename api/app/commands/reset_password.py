import argparse
import asyncio
import sys

from app.config import get_settings
from app.database import create_database_engine, create_session_factory
from app.services.admin import reset_password_transaction
from app.services.auth import FIXED_USERS


async def reset_password(name: str) -> str:
    if name not in dict(FIXED_USERS):
        raise ValueError("Name is not a fixed account")
    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as db:
            return await reset_password_transaction(
                db,
                actor_id=None,
                target_name=name,
                reason="server CLI reset",
                allow_admin=True,
            )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reset one fixed account password")
    parser.add_argument("name")
    args = parser.parse_args(argv)
    try:
        temporary_password = asyncio.run(reset_password(args.name))
    except Exception as exc:
        print(
            f"Unable to reset password ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    print(f"{args.name}: {temporary_password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
