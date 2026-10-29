"""One-shot jobs restored from PostgreSQL; no periodic Market polling."""

import asyncio
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from src.common.tasks import settle
from src.common.time import now
from src.database.locks import Busy
from src.database.models import Account
from src.services.accounts import AccountService
from src.services.errors import AccountNotFound
from src.services.farming import FarmingService
from src.services.notifications import NotificationService


class SchedulerService:
    def __init__(
        self,
        accounts: AccountService,
        farming: FarmingService,
        notifications: NotificationService,
        changes: asyncio.Queue[str] | None = None,
    ) -> None:
        self._accounts = accounts
        self._farming = farming
        self._notifications = notifications
        self._changes = changes if changes is not None else asyncio.Queue()
        self._scheduler = AsyncIOScheduler(timezone='UTC')
        self._active: set[asyncio.Task] = set()
        self._running: set[str] = set()
        self._not_before = {}
        self._watcher: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        self._scheduler.start(paused=True)
        # Database is the source of truth, including jobs missed while offline.
        for account in await self._accounts.list():
            self._plan(account)
        self._watcher = asyncio.create_task(self._watch_changes())
        self._scheduler.resume()

    def _plan(self, account: Account) -> None:
        if self._stopping or account.id in self._running:
            return
        identifier = 'account:' + account.id
        if not account.auto_collect or account.needs_review or account.auth_state != 'ready':
            if self._scheduler.get_job(identifier):
                self._scheduler.remove_job(identifier)
            return
        deadline = max(
            account.next_run_at,
            self._not_before.get(account.id, now()),
            now() + timedelta(seconds=1),
        )
        self._scheduler.add_job(
            self._run,
            'date',
            run_date=deadline,
            args=[account.id],
            id=identifier,
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=None,
        )

    def _forget(self, account_id: str) -> None:
        self._not_before.pop(account_id, None)
        identifier = 'account:' + account_id
        if self._scheduler.get_job(identifier):
            self._scheduler.remove_job(identifier)

    def _retry(self, account_id: str) -> None:
        if self._stopping:
            return
        deadline = now() + timedelta(seconds=60)
        self._not_before[account_id] = deadline
        self._scheduler.add_job(
            self._run,
            'date',
            run_date=deadline,
            args=[account_id],
            id='account:' + account_id,
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=None,
        )

    async def _watch_changes(self) -> None:
        while not self._stopping:
            account_id = await self._changes.get()
            try:
                self._plan(await self._accounts.get(account_id))
            except AccountNotFound:
                self._forget(account_id)
            except Exception as exc:
                logger.warning('Schedule refresh failed: {}', type(exc).__name__)
                self._retry(account_id)
            finally:
                self._changes.task_done()

    async def _run(self, account_id: str) -> None:
        if self._stopping:
            return
        task = asyncio.current_task()
        self._active.add(task)
        self._running.add(account_id)
        retry = False
        try:
            account = await self._accounts.get(account_id)
            if (
                not account.auto_collect
                or account.needs_review
                or account.auth_state != 'ready'
                or account.next_run_at > now()
            ):
                return
            result = await self._farming.run(account_id, trigger='schedule')
            self._not_before.pop(account_id, None)
            account = await self._accounts.get(account_id)
            if account.notifications and result.state != 'skipped':
                await self._notifications.report_run(result)
        except AccountNotFound:
            self._forget(account_id)
        except Busy:
            retry = True
        except Exception as exc:
            logger.error('Scheduled run failed: {}', type(exc).__name__)
            retry = True
        finally:
            self._running.discard(account_id)
            if not self._stopping:
                if retry:
                    self._retry(account_id)
                else:
                    try:
                        self._plan(await self._accounts.get(account_id))
                    except AccountNotFound:
                        self._forget(account_id)
                    except Exception as exc:
                        logger.warning('Schedule refresh failed: {}', type(exc).__name__)
                        self._retry(account_id)
            self._active.discard(task)

    async def stop(self) -> None:
        self._stopping = True
        if self._scheduler.running:
            self._scheduler.pause()
        if self._watcher:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
        if self._active:
            await settle(
                asyncio.ensure_future(asyncio.gather(*tuple(self._active), return_exceptions=True))
            )
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
