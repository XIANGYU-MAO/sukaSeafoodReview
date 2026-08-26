import asyncio
import sys

from sqlalchemy import select

from app.config import get_settings
from app.database import create_database_engine, create_session_factory
from app.models import Species


DEFAULT_SPECIES = (
    ("SF001", "Kembung / Pelaling", "Rastrelliger kanagurta", 1),
    ("SF002", "Bawal Hitam", "Parastromateus niger", 2),
    ("SF003", "Ikan Merah", "Lutjanus sebae", 3),
    ("SF004", "Tilapia", "Oreochromis niloticus", 4),
    ("SF005", "Kerapu Bintik", "Epinephelus coioides", 5),
)


async def seed_species() -> list[tuple[str, str, str]]:
    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    created: list[tuple[str, str, str]] = []
    try:
        async with session_factory() as db:
            existing_codes = set((await db.scalars(select(Species.code))).all())
            for code, app_label, scientific_name, sort_order in DEFAULT_SPECIES:
                if code in existing_codes:
                    continue
                db.add(
                    Species(
                        code=code,
                        name_zh=app_label,
                        name_en=app_label,
                        scientific_name=scientific_name,
                        active=True,
                        sort_order=sort_order,
                    )
                )
                created.append((code, app_label, scientific_name))
            await db.commit()
    finally:
        await engine.dispose()
    return created


def main() -> int:
    try:
        created = asyncio.run(seed_species())
    except Exception as exc:
        print(
            f"Unable to seed default species ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    for code, app_label, scientific_name in created:
        print(f"{code}: {app_label} ({scientific_name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
