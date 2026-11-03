import asyncio
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from src.database.models import Account, BonusStatus, FarmRun
from src.database.session import Database
from src.services.bonus import BonusService
from src.services.live_collector import LiveCollector
from tests.test_live_client import cursor


class Client:
    def __init__(self, bootstraps, *, end=False):
        self.bootstraps = list(bootstraps)
        self.end = end
        self.polls = []

    def check_auth(self, **kwargs):
        pass

    def balance(self):
        return 50

    def bootstrap_live(self):
        return deepcopy(self.bootstraps.pop(0))

    def poll_live(self, value):
        self.polls.append(value.stream_id)
        value.ended = self.end
        return value.state


def service(client):
    @contextmanager
    def factory(_):
        yield client

    return LiveCollector(factory, __import__('pathlib').Path('.'))


def test_end_switches_to_current_bootstrap_without_polling_old_stream_again():
    old, new = cursor(), cursor()
    new.stream_id = 'new-stream'
    c = Client([old, new], end=True)
    s = service(c)
    state, delay, error = s.poll('test', FarmRun(account_id='test'))
    assert not error and state.state == 'collecting'
    assert c.polls == ['stream-a']
    assert s.cursors['test'].cursor.stream_id == 'new-stream'
    assert 0 < delay <= 15
    c.end = False
    s.poll('test', FarmRun(account_id='test'))
    assert c.polls == ['stream-a', 'new-stream']


def test_same_ended_stream_waits_and_rechecks_without_replaying():
    c = Client([cursor(), cursor(), cursor()], end=True)
    s = service(c)
    state, delay, error = s.poll('test', FarmRun(account_id='test'))
    assert not error and state.state == 'waiting_stream' and delay == 60
    state, delay, error = s.poll('test', FarmRun(account_id='test'))
    assert state.state == 'waiting_stream' and delay == 60
    assert c.polls == ['stream-a']


def test_disabled_stream_rechecks_and_can_become_active():
    disabled = cursor()
    disabled.state.coinSettings.enabled = False
    c = Client([disabled, cursor()])
    s = service(c)
    assert s.poll('test', FarmRun(account_id='test'))[0].state == 'disabled'
    assert not c.polls
    assert s.poll('test', FarmRun(account_id='test'))[0].state == 'collecting'
    assert c.polls == ['stream-a']


def test_stalled_stream_discards_cursor_and_has_no_false_eta(monkeypatch):
    clock = [0]
    monkeypatch.setattr(
        'src.services.live_collector.time', SimpleNamespace(monotonic=lambda: clock[0])
    )
    c = Client([cursor()])
    s = service(c)
    s.poll('test', FarmRun(account_id='test'))
    clock[0] = 121
    state, delay, error = s.poll('test', FarmRun(account_id='test'))
    assert not error and state.state == 'stalled' and state.remaining_seconds is None
    assert delay == 60 and 'test' not in s.cursors


def test_eta_uses_actual_api_conditions_and_cap_stops_polling():
    value = cursor()
    value.state.balance = 0
    assert LiveCollector.status('test', value).remaining_seconds == 3750
    value.state.coinSettings.amount = 2
    value.state.balance = 7
    assert LiveCollector.status('test', value).remaining_seconds == 122 * 15
    value.state.balance = 250
    c = Client([value])
    state, delay, error = service(c).poll('test', FarmRun(account_id='test'))
    assert not error and state.state == 'limit' and state.remaining_seconds == 0
    assert delay == 3600 and not c.polls


def test_notifications_deduplicate_across_restart_and_reset_on_counter_reset(tmp_path):
    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'notice.db'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        await db.accounts.save(Account(id='test'))
        report = AsyncMock()

        def create():
            s = BonusService(
                db.accounts, db.runs, db.locks, None, tmp_path, live_factory=None, live=True
            )
            s._report = report
            return s

        s = create()
        done = BonusStatus(account_id='test', source='live', state='limit', balance=250, limit=250)
        await s._save_status(done, None)
        await s._notify(done, None)
        s = create()
        old = await s.status('test', 'live')
        await s._notify(old, None)
        assert report.await_count == 1
        fresh = BonusStatus(
            account_id='test', source='live', state='collecting', balance=0, limit=250
        )
        await s._save_status(fresh, old)
        assert fresh.cycle == 1
        done = BonusStatus(account_id='test', source='live', state='limit', balance=250, limit=250)
        await s._save_status(done, fresh)
        await s._notify(done, None)
        assert report.await_count == 2
        account = await db.accounts.get('test')
        account.notifications = False
        await db.accounts.save(account)
        failed = BonusStatus(
            account_id='test', source='live', state='failed', error_code='AuthRequired'
        )
        await s._save_status(failed, done)
        await s._notify(failed, None)
        assert report.await_count == 2
        await engine.dispose()

    asyncio.run(scenario())


def test_zero_reward_refreshes_settings_instead_of_caching_disabled_forever():
    disabled = cursor()
    disabled.state.coinSettings.amount = 0
    c = Client([disabled, cursor()])
    s = service(c)
    assert s.poll('test', FarmRun(account_id='test'))[0].state == 'disabled'
    assert not c.polls
    assert s.poll('test', FarmRun(account_id='test'))[0].state == 'collecting'
    assert c.polls == ['stream-a']


def test_manual_refresh_persists_server_cooldown_and_source_preferences(tmp_path):
    from src.market.errors import RateLimited

    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'refresh.db'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        await db.accounts.save(Account(id='test', auth_state='ready', auto_collect=False))
        calls = []

        @contextmanager
        def factory(_):
            calls.append('request')
            raise RateLimited(86400)
            yield  # pragma: no cover

        s = BonusService(
            db.accounts, db.runs, db.locks, None, tmp_path, live_factory=factory, live=True
        )
        old = BonusStatus(account_id='test', source='live', balance=35, limit=250)
        await db.runs.save(old)
        failed = await s.refresh_live('test')
        assert failed.error_code == 'RateLimited' and failed.balance == 35
        assert (failed.next_check_at - failed.checked_at).total_seconds() >= 86399
        restarted = BonusService(
            db.accounts, db.runs, db.locks, None, tmp_path, live_factory=factory, live=True
        )
        assert (await restarted.refresh_live('test')).next_check_at == failed.next_check_at
        assert calls == ['request']
        saved = await restarted.configure('test', 'live', False)
        assert not saved.live_auto and not saved.auto_collect
        assert (await db.accounts.get('test')).live_auto is False
        await engine.dispose()

    asyncio.run(scenario())


def test_stale_notification_cannot_overwrite_newer_snapshot(tmp_path):
    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'stale.db'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        await db.accounts.save(Account(id='test'))
        s = BonusService(
            db.accounts, db.runs, db.locks, None, tmp_path, live_factory=None, live=True
        )
        s._report = AsyncMock()
        old = BonusStatus(account_id='test', source='live', state='limit', balance=250, limit=250)
        await db.runs.save(old)
        new = BonusStatus(
            account_id='test', source='live', state='collecting', balance=0, limit=250
        )
        await s._save_status(new, old)
        await s._notify(old, None)
        stored = await s.status('test', 'live')
        assert stored.state == 'collecting' and stored.balance == 0 and stored.cycle == 1
        assert stored.notified_key is None
        s._report.assert_not_awaited()
        await engine.dispose()

    asyncio.run(scenario())
