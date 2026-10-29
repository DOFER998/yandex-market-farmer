import asyncio
from contextlib import contextmanager

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from src.market.models import DailyStatus
from src.services.accounts import AccountService


def test_service_journals_claim_and_disables_uncertain_account(tmp_path):
    from src.database.models import Account
    from src.database.session import Database
    from src.market.errors import UncertainClaim
    from src.market.models import Hub
    from src.services.farming import FarmingService

    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'db.sqlite'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        await db.accounts.save(Account(id='main'))
        loop = asyncio.get_running_loop()
        calls = []

        class Client:
            def check_auth(self):
                pass

            def daily_status(self):
                return DailyStatus(available=False)

            def balance(self):
                return 7

            def claim_daily(self):
                rows = asyncio.run_coroutine_threadsafe(db.runs.operations('main'), loop).result(3)
                assert any(row.kind == 'daily' and row.state == 'pending' for row in rows)
                calls.append('daily')
                raise UncertainClaim('private detail')

            def games(self):
                calls.append('games')
                return Hub(games=[], missions=[])

        @contextmanager
        def factory(_):
            yield Client()

        service = FarmingService(db.accounts, db.runs, db.locks, tmp_path, factory=factory)
        result = await service.run('main')
        assert result.state == 'uncertain'
        account = await db.accounts.get('main')
        assert account.needs_review and not account.auto_collect
        assert calls == ['daily']
        assert 'private' not in result.error_code
        assert (await service.run('main')).state == 'blocked'
        await engine.dispose()

    asyncio.run(scenario())


def test_service_unavailable_daily_does_not_collect_games(tmp_path):
    from src.database.models import Account
    from src.database.session import Database
    from src.market.models import DailyClaim
    from src.services.farming import FarmingService

    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'db.sqlite'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        await db.accounts.save(Account(id='main'))

        class Client:
            def check_auth(self):
                pass

            def daily_status(self):
                return DailyStatus(available=False)

            def balance(self):
                return 7

            def claim_daily(self):
                return DailyClaim(state='unavailable')

            def games(self):
                raise AssertionError('Daily collection must not request games')

        @contextmanager
        def factory(_):
            yield Client()

        service = FarmingService(db.accounts, db.runs, db.locks, tmp_path, factory=factory)
        result = await service.run('main')
        assert result.state == 'unavailable'
        assert result.balance_before == result.balance_after == 7
        assert (await db.accounts.get('main')).auth_state == 'ready'
        assert [x.state for x in await db.runs.operations('main') if x.kind == 'daily'] == [
            'unavailable'
        ]
        await engine.dispose()

    asyncio.run(scenario())


def test_interrupted_run_blocks_mutations_until_explicit_reconcile(tmp_path):
    from src.database.models import Account, FarmRun
    from src.database.session import Database
    from src.services.farming import FarmingService

    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'db.sqlite'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        await db.accounts.save(Account(id='main', auto_collect=True, auth_state='ready'))
        await db.runs.save(FarmRun(account_id='main'))
        calls = []

        class Client:
            def check_auth(self):
                calls.append('auth')

            def daily_status(self):
                return DailyStatus(available=False)

            def balance(self):
                return 9

        @contextmanager
        def factory(_):
            yield Client()

        service = FarmingService(db.accounts, db.runs, db.locks, tmp_path, factory=factory)
        assert (await service.run('main')).state == 'blocked'
        assert calls == []
        assert not (await db.accounts.get('main')).auto_collect
        assert (await db.runs.history('main'))[0].state == 'interrupted'
        await service.run('main', read_only=True)
        assert (await db.accounts.get('main')).needs_review
        await service.run('main', read_only=True, reconcile=True)
        account = await db.accounts.get('main')
        assert not account.needs_review and not account.auto_collect
        await AccountService(db.accounts, db.runs, db.locks, tmp_path).configure(
            'main', enabled=True, minutes=60
        )
        assert (await db.accounts.get('main')).auto_collect
        await engine.dispose()

    asyncio.run(scenario())


def test_cancellation_keeps_account_lock_until_worker_finishes(tmp_path):
    import threading

    import pytest

    from src.database.locks import Busy
    from src.database.models import Account
    from src.database.session import Database
    from src.services.farming import FarmingService

    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'db.sqlite'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        await db.accounts.save(Account(id='main'))
        entered, release = threading.Event(), threading.Event()

        class Client:
            def check_auth(self):
                pass

            def daily_status(self):
                return DailyStatus(available=False)

            def balance(self):
                entered.set()
                assert release.wait(3)
                return 7

        @contextmanager
        def factory(_):
            yield Client()

        service = FarmingService(db.accounts, db.runs, db.locks, tmp_path, factory=factory)
        task = asyncio.create_task(service.run('main', read_only=True))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        with pytest.raises(Busy):
            await service.run('main')
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await service.run('main')).state == 'blocked'
        await engine.dispose()

    asyncio.run(scenario())


def test_game_schema_failure_is_partial_and_other_game_can_claim(tmp_path):
    from src.database.models import Account
    from src.database.session import Database
    from src.market.errors import ProtocolError
    from src.market.models import DailyClaim, Game, GameClaim, Hub
    from src.services.farming import FarmingService

    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'db.sqlite'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        await db.accounts.save(Account(id='main'))
        calls = []

        class Client:
            def check_auth(self):
                pass

            def daily_status(self):
                return DailyStatus(available=False)

            def balance(self):
                return 7

            def claim_daily(self):
                return DailyClaim(state='unavailable')

            def games(self):
                return Hub(games=[Game(game_id=x, token='secret') for x in ('a', 'b')], missions=[])

            def claim_game(self, game):
                calls.append(game)
                if game == 'a':
                    raise ProtocolError('schema changed')
                return GameClaim(claimed=['bronze'], unconfirmed=[])

        @contextmanager
        def factory(_):
            yield Client()

        run = await FarmingService(db.accounts, db.runs, db.locks, tmp_path, factory=factory).run(
            'main', target='games'
        )
        assert run.state == 'partial'
        assert calls == ['a', 'b']
        assert any(
            row.kind == 'game:b' and row.state == 'claimed'
            for row in await db.runs.operations('main')
        )
        await engine.dispose()

    asyncio.run(scenario())


def test_repeated_cancellation_cannot_release_lock_while_worker_runs(tmp_path):
    import threading

    import pytest

    from src.database.locks import Busy
    from src.database.models import Account
    from src.database.session import Database
    from src.services.farming import FarmingService

    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'db.sqlite'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        await db.accounts.save(Account(id='main'))
        entered, release = threading.Event(), threading.Event()

        class Client:
            def check_auth(self):
                pass

            def daily_status(self):
                return DailyStatus(available=False)

            def balance(self):
                entered.set()
                release.wait(3)
                return 7

        @contextmanager
        def factory(_):
            yield Client()

        service = FarmingService(db.accounts, db.runs, db.locks, tmp_path, factory=factory)
        task = asyncio.create_task(service.run('main', read_only=True))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.02)
        try:
            assert db.locks._locks['main'].locked()
            with pytest.raises(Busy):
                await service.run('main')
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0.05)
            await engine.dispose()

    asyncio.run(scenario())


def test_ambiguous_pg_lock_acquisition_invalidates_connection():
    from types import SimpleNamespace

    import pytest

    from src.database.session import Database

    async def scenario():
        class Connection:
            invalidated = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def scalar(self, *args):
                return True

            async def commit(self):
                raise RuntimeError('connection lost')

            async def invalidate(self):
                self.invalidated = True

        conn = Connection()
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'), connect=lambda: conn)
        db = Database(engine)
        with pytest.raises(RuntimeError):
            async with db.locks.guard('main'):
                pass
        assert conn.invalidated

    asyncio.run(scenario())


def test_daily_and_games_have_separate_execution_and_schedule(tmp_path):
    from datetime import timedelta

    from src.database.models import Account, now
    from src.database.session import Database
    from src.market.models import DailyClaim, Hub
    from src.services.farming import FarmingService

    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'separate.sqlite'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        deadline = now() + timedelta(hours=2)
        await db.accounts.save(Account(id='main', daily_reward_at=deadline, next_run_at=deadline))
        calls = []
        fail_games = False

        class Client:
            def check_auth(self):
                pass

            def balance(self):
                return 37

            def claim_daily(self):
                calls.append('daily')
                return DailyClaim(state='unavailable', next_reward_at=deadline)

            def games(self):
                calls.append('games')
                if fail_games:
                    from src.market.errors import ProtocolError

                    raise ProtocolError('unexpected game schema')
                return Hub(games=[], missions=[])

        @contextmanager
        def factory(_):
            yield Client()

        service = FarmingService(db.accounts, db.runs, db.locks, tmp_path, factory=factory)
        daily = await service.run('main')
        assert calls == ['daily']
        assert daily.state == 'unavailable'
        schedule = (await db.accounts.get('main')).next_run_at
        calls.clear()
        games = await service.run('main', target='games')
        assert calls == ['games']
        assert games.trigger == 'games'
        assert games.state == 'no_rewards'
        assert (await db.accounts.get('main')).next_run_at == schedule
        fail_games = True
        failed = await service.run('main', target='games')
        assert failed.state == 'failed'
        assert (await db.accounts.get('main')).next_run_at == schedule
        details = await AccountService(db.accounts, db.runs, db.locks, tmp_path).run_operations(
            failed
        )
        assert any(op.kind == 'games' and op.error_code == 'ProtocolError' for op in details)
        assert all(op.run_id == failed.id for op in details)
        await engine.dispose()

    asyncio.run(scenario())
