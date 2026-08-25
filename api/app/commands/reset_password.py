import argparse
import asyncio
import sys

from sqlalchemy import update

from app.config import get_settings
from app.database import create_database_engine, create_session_factory
from app.models import Session
from app.services.auth import (
    FIXED_USERS,
    generate_temporary_password,
    hash_password,
    user_by_name_for_update,
    utc_now,
)


async def reset_password(name: str) -> str:
    if name not in dict(FIXED_USERS):
        raise ValueError("Name is not a fixed account")
    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as db:
            user = await db.scalar(user_by_name_for_update(name))
            if user is None:
                raise RuntimeError("Fixed accounts have not been initialized")
            temporary_password = generate_temporary_password()
            user.password_hash = hash_password(temporary_password)
            user.password_version += 1
            user.must_change_password = True
            user.failed_login_count = 0
            user.locked_until = None
            await db.execute(
                update(Session)
                .where(Session.user_id == user.id, Session.revoked_at.is_(None))
                .values(revoked_at=utc_now())
            )
            await db.commit()
            return temporary_password
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
