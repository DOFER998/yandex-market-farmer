import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.common.tasks import settle
from src.market.errors import MarketError


class Busy(MarketError):
    pass


class AccountLocks:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def guard(self, account_id: str) -> AsyncIterator[None]:
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        if lock.locked():
            raise Busy('Account is busy')
        async with lock:
            if self.engine.dialect.name != 'postgresql':
                yield  # SQLite test harness only; server config requires PostgreSQL.
                return
            async with self.engine.connect() as conn:
                try:
                    acquired = await conn.scalar(
                        text('SELECT pg_try_advisory_lock(791342, hashtext(:key))'),
                        {'key': account_id},
                    )
                    await conn.commit()
                except BaseException:
                    # Even acquisition can have succeeded remotely before the connection failed.
                    await settle(asyncio.create_task(conn.invalidate()))
                    raise
                if not acquired:
                    raise Busy('Account is busy')

                async def release():
                    try:
                        await conn.execute(
                            text('SELECT pg_advisory_unlock(791342, hashtext(:key))'),
                            {'key': account_id},
                        )
                        await conn.commit()
                    except BaseException:
                        await conn.invalidate()
                        raise

                try:
                    yield
                finally:
                    _, cancelled = await settle(asyncio.create_task(release()))
                    if cancelled:
                        raise asyncio.CancelledError
