from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _is_postgresql(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


async def acquire_sync_generation_lock(session: AsyncSession) -> None:
    """Serialize generation/history/export writes before any row lock is taken.

    ``LOCK TABLE`` is deliberately a PostgreSQL utility command: an exporter
    can wait here inside REPEATABLE READ without fixing an obsolete MVCC
    snapshot.  SHARE UPDATE EXCLUSIVE conflicts with itself and the epoch
    migration's SHARE ROW EXCLUSIVE mode, but remains compatible with this
    transaction's later ordinary DML locks.
    """

    if _is_postgresql(session):
        await session.execute(
            text("LOCK TABLE export_batches IN SHARE UPDATE EXCLUSIVE MODE")
        )
