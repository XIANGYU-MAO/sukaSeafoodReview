import argparse
import asyncio
import sys

from sqlalchemy import select

from app.config import get_settings
from app.database import create_database_engine, create_session_factory
from app.models import User
from app.services.auth import FIXED_USERS, generate_temporary_password, hash_password


async def seed_users(print_once: bool) -> list[tuple[str, str]]:
    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    created: list[tuple[str, str]] = []
    try:
        async with session_factory() as db:
            existing_users = list((await db.scalars(select(User))).all())
            expected = dict(FIXED_USERS)
            unexpected = [user.name for user in existing_users if user.name not in expected]
            if unexpected:
                raise RuntimeError("Database contains accounts outside the fixed list")
            existing = {user.name: user for user in existing_users}
            for name, role in FIXED_USERS:
                if name in existing:
                    if existing[name].role != role:
                        raise RuntimeError("Fixed account role does not match configuration")
                    continue
                temporary_password = generate_temporary_password()
                db.add(
                    User(
                        name=name,
                        role=role,
                        password_hash=hash_password(temporary_password),
                        must_change_password=True,
                    )
                )
                created.append((name, temporary_password))
            await db.commit()
    finally:
        await engine.dispose()
    return created if print_once else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the six fixed accounts")
    parser.add_argument("--print-once", action="store_true")
    args = parser.parse_args(argv)
    try:
        created = asyncio.run(seed_users(args.print_once))
    except Exception as exc:
        print(
            f"Unable to seed fixed accounts ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    for name, password in created:
        print(f"{name}: {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
