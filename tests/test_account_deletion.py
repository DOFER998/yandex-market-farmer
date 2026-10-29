import asyncio

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select

from src.database.models import Account, BalanceSnapshot, FarmRun, Operation
from src.database.session import Database
from src.services.accounts import AccountService


def test_delete_removes_only_selected_account_and_dependents(tmp_path):
    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'db.sqlite'))

        @event.listens_for(engine.sync_engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')

        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        sessions = tmp_path / 'sessions'
        sessions.mkdir(mode=0o700)
        changes = []
        service = AccountService(db.accounts, db.runs, db.locks, sessions, changed=changes.append)
        for name in ('main', 'other'):
            await db.accounts.save(Account(id=name, auto_collect=True, auth_state='ready'))
            run = FarmRun(account_id=name)
            await db.runs.save(run)
            await db.runs.save(
                Operation(account_id=name, run_id=run.id, kind='daily'),
                BalanceSnapshot(account_id=name, run_id=run.id, coins=10),
            )
            (sessions / f'{name}.txt').write_text('private')
        await service.delete('main')
        assert [a.id for a in await service.list()] == ['other']
        assert not (sessions / 'main.txt').exists()
        assert (sessions / 'other.txt').read_text() == 'private'
        assert changes[-1] == 'main'
        async with db.accounts.session() as session:
            for model in (FarmRun, Operation, BalanceSnapshot):
                rows = (await session.exec(select(model))).all()
                assert len(rows) == 1 and rows[0].account_id == 'other'
        # Busy account must not lose its session or database row.
        from src.database.locks import Busy

        async with db.locks.guard('other'):
            with pytest.raises(Busy):
                await service.delete('other')
        assert (sessions / 'other.txt').exists()
        await engine.dispose()

    asyncio.run(scenario())


def test_verified_session_attachment_disables_autofarm_and_records_balance(tmp_path):
    from http.cookiejar import Cookie, CookieJar

    from src.market.session import SessionStore

    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'attach.sqlite'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        account = Account(id='main', auth_state='expired', auto_collect=True, needs_review=True)
        await db.accounts.save(account)
        sessions = tmp_path / 'sessions'
        service = AccountService(db.accounts, db.runs, db.locks, sessions)
        jar = CookieJar()
        jar.set_cookie(
            Cookie(
                0,
                'Session_id',
                'test-only',
                None,
                False,
                '.yandex.ru',
                True,
                True,
                '/',
                True,
                True,
                None,
                True,
                None,
                None,
                {},
            )
        )
        result = await service.attach_verified('main', jar, 96)
        assert result.auth_state == 'ready'
        assert not result.auto_collect
        assert result.needs_review  # Login must not clear an uncertain collection.
        assert (await service.latest_run('main')).balance_after == 96
        with SessionStore(sessions / 'main.txt') as store:
            assert any(c.name == 'Session_id' for c in store.load())
        await engine.dispose()

    asyncio.run(scenario())
