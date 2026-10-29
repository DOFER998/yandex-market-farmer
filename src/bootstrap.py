"""Composition root. Concrete adapters are wired only here."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial

from sqlalchemy.ext.asyncio import create_async_engine

from src.common.config import Environment
from src.database.session import Database
from src.market.factory import account_client
from src.services.accounts import AccountService
from src.services.farming import FarmingService


@dataclass(frozen=True, slots=True)
class Application:
    database: Database
    accounts: AccountService
    farming: FarmingService
    schedule_changes: asyncio.Queue[str]


@asynccontextmanager
async def application(settings: Environment):
    engine = create_async_engine(settings.postgres.dsn, pool_pre_ping=True, hide_parameters=True)
    try:
        db = Database(engine)
        changes: asyncio.Queue[str] = asyncio.Queue()
        yield Application(
            database=db,
            accounts=AccountService(
                db.accounts,
                db.runs,
                db.locks,
                settings.market.session_dir,
                changed=changes.put_nowait,
            ),
            farming=FarmingService(
                db.accounts,
                db.runs,
                db.locks,
                settings.market.session_dir,
                factory=partial(account_client, interval=settings.market.interval_seconds),
                changed=changes.put_nowait,
            ),
            schedule_changes=changes,
        )
    finally:
        await engine.dispose()
