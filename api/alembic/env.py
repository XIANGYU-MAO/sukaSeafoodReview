import asyncio
from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.models import Base


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def configure_and_run(connection: Connection) -> None:
    migration_schema = config.get_main_option("migration_schema")
    if migration_schema:
        quoted_schema = connection.dialect.identifier_preparer.quote(migration_schema)
        connection.execute(text(f"SET search_path TO {quoted_schema}"))
        connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(configure_and_run)
    await connectable.dispose()


def run_migrations_online() -> None:
    url = make_url(config.get_main_option("sqlalchemy.url"))
    if url.drivername in {"sqlite+aiosqlite", "postgresql+asyncpg"}:
        asyncio.run(run_async_migrations())
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        configure_and_run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
