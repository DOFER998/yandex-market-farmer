import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from src.database.models import Account
from src.database.session import Database
from src.market.errors import AuthRequired, ProtocolError
from src.services.bonus import BonusService
from tests.test_live_client import cursor


@pytest.mark.parametrize('failure', [None, ProtocolError('schema'), AuthRequired('expired')])
def test_live_tick_is_isolated_from_daily_and_persists_outcome(tmp_path, failure):
    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'live.db'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        account = Account(
            id='main',
            auto_collect=True,
            auth_state='ready',
            daily_needs_review=True,
            games_needs_review=True,
        )
        deadline = account.next_run_at
        await db.accounts.save(account)
        state = cursor()
        calls = []

        class Client:
            def check_auth(self, **kwargs):
                calls.append('auth')

            def balance(self):
                return 50 + state.state.balance

            def bootstrap_live(self):
                return state

            def poll_live(self, value):
                calls.append('poll')
                if failure:
                    raise failure
                value.state.balance += 1
                return value.state

        @contextmanager
        def factory(path):
            yield Client()

        farming = AsyncMock()
        service = BonusService(
            db.accounts, db.runs, db.locks, farming, tmp_path, live_factory=factory, live=True
        )
        delay = await service.tick_live('main')
        stored = await db.accounts.get('main')
        assert stored.next_run_at == deadline
        assert stored.daily_needs_review and stored.games_needs_review
        assert stored.auto_collect == (not isinstance(failure, AuthRequired))
        assert calls == ['auth', 'poll']
        farming.run.assert_not_awaited()
        history = await db.runs.history('main')
        assert len(history) == 1 and history[0].trigger == 'live'
        assert history[0].state == ('failed' if failure else 'success')
        if not failure:
            assert 0 < delay <= 15
            assert history[0].balance_after - history[0].balance_before == 1
        # Disabling autocollect prevents further requests, including cached cursors.
        stored.auto_collect = False
        await db.accounts.save(stored)
        await service.tick_live('main')
        assert calls == ['auth', 'poll']
        await service.stop()
        await engine.dispose()

    asyncio.run(scenario())
