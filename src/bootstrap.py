"""Composition root. Concrete adapters are wired only here."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from functools import partial

from sqlalchemy.ext.asyncio import create_async_engine

from src.common.config import Environment
from src.database.session import Database
from src.market.factory import account_client, live_client
from src.services.accounts import AccountService
from src.services.bonus import BonusService
from src.services.farming import FarmingService


@dataclass(frozen=True, slots=True)
class Application:
    database: Database
    accounts: AccountService
    farming: FarmingService
    schedule_changes: asyncio.Queue[str]
    bonus: BonusService | None = None


@asynccontextmanager
async def application(settings: Environment):
    engine = create_async_engine(settings.postgres.dsn, pool_pre_ping=True, hide_parameters=True)
    try:
        db = Database(engine)
        changes: asyncio.Queue[str] = asyncio.Queue()
        app = Application(
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
                factory=partial(
                    account_client,
                    interval=settings.market.interval_seconds,
                    submit_game_results=settings.market.submit_game_results,
                ),
                changed=changes.put_nowait,
            ),
            schedule_changes=changes,
        )
        bonus = BonusService(
            db.accounts,
            db.runs,
            db.locks,
            app.farming,
            settings.market.session_dir,
            live_factory=partial(live_client, interval=settings.market.interval_seconds),
            games=settings.market.auto_games and settings.market.submit_game_results,
            live=settings.market.auto_live,
        )
        yield replace(app, bonus=bonus)
    finally:
        await engine.dispose()
