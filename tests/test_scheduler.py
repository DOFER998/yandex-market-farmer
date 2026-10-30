import asyncio
from datetime import timedelta

from apscheduler.triggers.date import DateTrigger

from src.common.time import now
from src.database.models import Account, FarmRun
from src.services.scheduler import SchedulerService


class Accounts:
    def __init__(self, *accounts):
        self.rows = {account.id: account for account in accounts}

    async def list(self):
        return list(self.rows.values())

    async def get(self, account_id):
        return self.rows[account_id]


class Notifications:
    def __init__(self):
        self.sent = []

    async def report_run(self, run):
        self.sent.append(run.account_id)


def test_scheduler_restores_date_jobs_and_updates_or_cancels_on_change():
    async def scenario():
        deadline = now() + timedelta(hours=2)
        accounts = Accounts(
            Account(id='main', auth_state='ready', auto_collect=True, next_run_at=deadline),
            Account(id='off'),
        )
        changes = asyncio.Queue()
        scheduler = SchedulerService(accounts, None, Notifications(), changes)
        await scheduler.start()
        jobs = scheduler._scheduler.get_jobs()
        assert len(jobs) == 1
        assert isinstance(jobs[0].trigger, DateTrigger)
        assert jobs[0].next_run_time == deadline
        accounts.rows['main'].next_run_at += timedelta(hours=1)
        changes.put_nowait('main')
        await changes.join()
        assert scheduler._scheduler.get_job('account:main').next_run_time == deadline + timedelta(
            hours=1
        )
        accounts.rows['main'].auto_collect = False
        changes.put_nowait('main')
        await changes.join()
        assert not scheduler._scheduler.get_jobs()
        await scheduler.stop()

    asyncio.run(scenario())


def test_due_job_reads_new_deadline_and_retry_does_not_form_tight_loop():
    async def scenario():
        accounts = Accounts(
            Account(id='main', auth_state='ready', auto_collect=True),
            Account(id='broken', auth_state='ready', auto_collect=True),
        )
        notifications = Notifications()
        calls = []

        class Farming:
            async def run(self, account_id, *, trigger):
                calls.append(account_id)
                if account_id == 'broken':
                    raise RuntimeError('private value')
                accounts.rows[account_id].next_run_at = now() + timedelta(days=1)
                return FarmRun(account_id=account_id, state='success')

        scheduler = SchedulerService(accounts, Farming(), notifications)
        await scheduler.start()
        await scheduler._run('main')
        job = scheduler._scheduler.get_job('account:main')
        assert job.next_run_time == accounts.rows['main'].next_run_at
        assert notifications.sent == ['main']
        before = now()
        await scheduler._run('broken')
        assert scheduler._scheduler.get_job('account:broken').next_run_time >= before + timedelta(
            seconds=60
        )
        await scheduler._run('main')  # Rechecks persisted deadline before calling Market.
        assert calls == ['main', 'broken']
        await scheduler.stop()

    asyncio.run(scenario())


def test_deleted_account_removes_job_without_retry():
    from src.services.errors import AccountNotFound

    class DeletableAccounts(Accounts):
        async def get(self, account_id):
            if account_id not in self.rows:
                raise AccountNotFound('deleted')
            return self.rows[account_id]

    async def scenario():
        accounts = DeletableAccounts(
            Account(
                id='main',
                auth_state='ready',
                auto_collect=True,
                next_run_at=now() + timedelta(hours=1),
            )
        )
        changes = asyncio.Queue()
        scheduler = SchedulerService(accounts, None, Notifications(), changes)
        await scheduler.start()
        del accounts.rows['main']
        changes.put_nowait('main')
        await changes.join()
        assert not scheduler._scheduler.get_jobs()
        await scheduler._run('main')
        assert not scheduler._scheduler.get_jobs()
        assert not scheduler._not_before
        await scheduler.stop()

    asyncio.run(scenario())


def test_pending_reward_review_keeps_daily_job_scheduled():
    async def scenario():
        accounts = Accounts(
            Account(id='daily', auth_state='ready', auto_collect=True, daily_needs_review=True),
            Account(id='games', auth_state='ready', auto_collect=True, games_needs_review=True),
        )
        scheduler = SchedulerService(accounts, None, Notifications())
        await scheduler.start()
        assert {job.id for job in scheduler._scheduler.get_jobs()} == {
            'account:daily',
            'account:games',
        }
        await scheduler.stop()

    asyncio.run(scenario())
