import asyncio
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from src.common.time import now
from src.database.models import Account, FarmRun, Operation
from src.database.session import Database
from src.market.errors import UncertainClaim
from src.market.models import DailyClaim, DailyStatus, Game, GameClaim, Hub, Level
from src.services.farming import FarmingService


def test_unknown_game_claim_cannot_disable_daily_and_is_not_replayed(tmp_path):
    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'games.db'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        await db.accounts.save(Account(id='main', auto_collect=True, auth_state='ready'))
        calls = []
        claimable = True

        class Client:
            def check_auth(self):
                pass

            def balance(self):
                return 30

            def wheel_missions(self):
                return []

            def games(self):
                return Hub(games=[Game(game_id='ballz', token='fake')], missions=[])

            def game_levels(self, game_id):
                calls.append('levels')
                return [
                    Level(
                        level='RESULT_LEVEL_BRONZE',
                        requiredScore=100,
                        rewardPoints=5,
                        isAchieved=not claimable,
                    )
                ]

            def claim_game(self, game_id):
                calls.append('game_claim')
                return GameClaim(claimed=[], unconfirmed=['bronze'])

            def claim_daily(self):
                calls.append('daily_claim')
                return DailyClaim(
                    state='claimed', amount=10, next_reward_at=now() + timedelta(days=1)
                )

        @contextmanager
        def factory(_):
            yield Client()

        service = FarmingService(db.accounts, db.runs, db.locks, tmp_path, factory=factory)
        assert (await service.run('main', target='games')).state == 'uncertain'
        account = await db.accounts.get('main')
        assert (
            account.auto_collect and account.games_needs_review and not account.daily_needs_review
        )
        assert (await service.run('main', trigger='schedule')).state == 'success'
        assert (await db.accounts.get('main')).auto_collect
        assert (await service.run('main', target='games')).error_code == 'GameReviewRequired'
        assert calls.count('game_claim') == 1
        assert calls.count('daily_claim') == 1
        claimable = False
        assert (await service.run('main', target='games')).state == 'reconciled'
        assert not (await db.accounts.get('main')).games_needs_review
        assert calls.count('game_claim') == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_daily_unknown_result_only_reads_until_unavailability_is_observed(tmp_path):
    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'daily.db'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        await db.accounts.save(Account(id='main', auto_collect=True, auth_state='ready'))
        claims = []
        available = True
        deadline = now() + timedelta(days=1)

        class Client:
            def check_auth(self):
                pass

            def balance(self):
                return 30

            def claim_daily(self):
                claims.append(1)
                raise UncertainClaim('network timeout')

            def daily_status(self):
                return DailyStatus(available=available, next_reward_at=deadline)

        @contextmanager
        def factory(_):
            yield Client()

        service = FarmingService(db.accounts, db.runs, db.locks, tmp_path, factory=factory)
        assert (await service.run('main')).state == 'uncertain'
        before = (await db.accounts.get('main')).next_run_at
        assert (await service.run('main')).state == 'review_pending'
        account = await db.accounts.get('main')
        assert account.auto_collect and account.daily_needs_review
        assert account.next_run_at > before
        # Recreate service to ensure the pending guard survives process replacement.
        service = FarmingService(db.accounts, db.runs, db.locks, tmp_path, factory=factory)
        available = False
        assert (await service.run('main')).state == 'reconciled'
        account = await db.accounts.get('main')
        assert account.auto_collect and not account.daily_needs_review
        assert account.daily_reward_at == deadline
        assert 30 <= (account.next_run_at - deadline).total_seconds() <= 90
        assert len(claims) == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_recover_scopes_interrupted_games_and_ignores_read_only_runs(tmp_path):
    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'recover.db'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        for trigger in ('games', 'read', 'live'):
            account = Account(id=trigger, auto_collect=True, auth_state='ready')
            await db.accounts.save(account)
            run = FarmRun(account_id=trigger, trigger=trigger)
            await db.runs.save(run)
            if trigger == 'games':
                await db.runs.save(Operation(account_id=trigger, run_id=run.id, kind='game:ballz'))
            await db.runs.recover(account)
            stored = await db.accounts.get(trigger)
            assert stored.auto_collect and not stored.needs_review and not stored.daily_needs_review
            assert stored.games_needs_review == (trigger == 'games')
            assert stored.games_review_game == ('ballz' if trigger == 'games' else None)
        await engine.dispose()

    asyncio.run(scenario())


def test_interrupted_mission_preserves_identifier_without_blocking_daily(tmp_path):
    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'mission.db'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        account = Account(id='main', auto_collect=True, auth_state='ready')
        await db.accounts.save(account)
        run = FarmRun(account_id='main', trigger='games')
        await db.runs.save(run)
        key = 'mission:hexagon_two_towers_5_wheel_november_2026_w2'
        await db.runs.save(Operation(account_id='main', run_id=run.id, kind=key))
        await db.runs.recover(account)
        stored = await db.accounts.get('main')
        assert stored.games_review_game == key
        assert stored.games_needs_review
        assert stored.auto_collect and not stored.daily_needs_review
        await engine.dispose()

    asyncio.run(scenario())


def test_automatic_games_propagate_rate_limit_without_changing_daily(tmp_path):
    import pytest

    from src.market.errors import RateLimited

    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'rate.db'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        account = Account(id='main', auto_collect=True, auth_state='ready')
        deadline = account.next_run_at
        await db.accounts.save(account)

        class Client:
            def check_auth(self):
                raise RateLimited(86400)

        @contextmanager
        def factory(_):
            yield Client()

        service = FarmingService(db.accounts, db.runs, db.locks, tmp_path, factory=factory)
        with pytest.raises(RateLimited) as error:
            await service.run('main', target='games', trigger='bonus')
        assert error.value.retry_after == 86400
        assert (await db.accounts.get('main')).next_run_at == deadline
        assert (await db.runs.history('main'))[0].error_code == 'RateLimited'
        await engine.dispose()

    asyncio.run(scenario())


def test_unknown_wheel_task_reconciles_without_replaying_or_reading_game_hub(tmp_path):
    from src.market.wheel_missions import WheelMission, WheelMissionAttempt

    async def scenario():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'wheel.db'))
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        db = Database(engine)
        await db.accounts.save(Account(id='main', auto_collect=True, auth_state='ready'))
        calls = []
        completed = False

        class Client:
            def check_auth(self):
                pass

            def balance(self):
                return 20

            def games(self):
                calls.append('hub')
                return Hub(games=[], missions=[])

            def wheel_missions(self):
                calls.append('wheel_read')
                return [
                    WheelMission(
                        mission_id='wheel_open_help_2026_v2',
                        reward=10,
                        status='completed' if completed else 'new',
                    )
                ]

            def complete_free_wheel_mission(self, mission_id):
                calls.append('wheel_click')
                return WheelMissionAttempt(mission_id=mission_id, state='unconfirmed')

        @contextmanager
        def factory(_):
            yield Client()

        service = FarmingService(db.accounts, db.runs, db.locks, tmp_path, factory=factory)
        assert (await service.run('main', target='games')).state == 'uncertain'
        saved = await db.accounts.get('main')
        assert saved.games_review_game == 'wheel:wheel_open_help_2026_v2'
        assert saved.auto_collect and not saved.daily_needs_review
        completed = True
        assert (await service.run('main', target='games')).state == 'reconciled'
        assert calls.count('wheel_click') == 1 and calls.count('hub') == 1
        assert not (await db.accounts.get('main')).games_needs_review
        await engine.dispose()

    asyncio.run(scenario())
