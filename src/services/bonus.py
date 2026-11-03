"""Independent source jobs with durable status, cooldowns and concise notifications."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path

from loguru import logger

from src.common.tasks import settle
from src.common.time import now
from src.database.locks import Busy
from src.database.models import Account, BalanceSnapshot, BonusStatus, FarmRun
from src.market.errors import AccessDenied, AuthRequired, MarketError, RateLimited, SessionError
from src.services.errors import AccountNotFound
from src.services.farming import FarmingService
from src.services.identity import validate_id
from src.services.live_collector import LiveCollector
from src.services.ports import Accounts, LiveClientFactory, Locks, Runs

Reporter = Callable[[BonusStatus, FarmRun | None], Awaitable[None]]


class BonusService:
    def __init__(
        self,
        accounts: Accounts,
        runs: Runs,
        locks: Locks,
        farming: FarmingService,
        session_dir: Path,
        *,
        live_factory: LiveClientFactory,
        games: bool = False,
        live: bool = False,
    ) -> None:
        self._accounts, self._runs, self._locks = accounts, runs, locks
        self._farming = farming
        self._collector = LiveCollector(live_factory, session_dir)
        self._enabled = {'games': games, 'live': live}
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._watcher: asyncio.Task | None = None
        self._report: Reporter | None = None
        self._stopping = False

    def available(self, source: str) -> bool:
        return self._enabled.get(source, False)

    def _active(self, account: Account, source: str) -> bool:
        return (
            self.available(source)
            and account.auto_collect
            and not account.needs_review
            and account.auth_state == 'ready'
            and getattr(account, source + '_auto')
        )

    async def status(self, account_id: str, source: str) -> BonusStatus | None:
        if source not in self._enabled:
            raise ValueError('Unknown source')
        return await self._runs.bonus_status(validate_id(account_id), source)

    async def configure(self, account_id: str, source: str, enabled: bool) -> Account:
        if source not in self._enabled:
            raise ValueError('Unknown source')
        async with self._locks.guard(validate_id(account_id)):
            account = await self._accounts.get(account_id)
            setattr(account, source + '_auto', enabled)
            await self._accounts.save(account)
        # Wake promptly on configuration changes; the saved cooldown is still respected.
        task = self._tasks.pop((account_id, source), None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._watcher and self._active(account, source) and not self._stopping:
            self._tasks[(account_id, source)] = asyncio.create_task(self._loop(account_id, source))
        return account

    async def start(self, report: Reporter | None = None) -> None:
        self._report = report
        if any(self._enabled.values()):
            self._watcher = asyncio.create_task(self._watch())

    async def _watch(self) -> None:
        while not self._stopping:
            try:
                accounts = await self._accounts.list()
                wanted = {
                    (a.id, source)
                    for a in accounts
                    for source in self._enabled
                    if self._active(a, source)
                }
                for key, task in list(self._tasks.items()):
                    if key not in wanted:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        self._tasks.pop(key, None)
                    elif task.done():
                        self._tasks.pop(key, None)
                for key in wanted:
                    if key not in self._tasks:
                        self._tasks[key] = asyncio.create_task(self._loop(*key))
            except Exception as exc:
                logger.warning('Bonus discovery failed: error={}', type(exc).__name__)
            await asyncio.sleep(30)

    async def _loop(self, account_id: str, source: str) -> None:
        try:
            while not self._stopping:
                try:
                    account = await self._accounts.get(account_id)
                    if not self._active(account, source):
                        return
                    status = await self.status(account_id, source)
                    if status and status.next_check_at and status.next_check_at > now():
                        await asyncio.sleep((status.next_check_at - now()).total_seconds())
                    if source == 'games':
                        await self.run_games(account_id, automatic=True)
                    else:
                        await self.tick_live(account_id)
                except AccountNotFound:
                    return
                except Busy:
                    await asyncio.sleep(5)
                except Exception as exc:
                    logger.warning(
                        'Bonus job failed: account={} source={} error={}',
                        account_id,
                        source,
                        type(exc).__name__,
                    )
                    await asyncio.sleep(300)
        finally:
            if source == 'live':
                self._collector.forget(account_id)

    async def run_games(self, account_id: str, *, automatic: bool = False) -> FarmRun:
        account = await self._accounts.get(validate_id(account_id))
        if automatic and not self._active(account, 'games'):
            return FarmRun(account_id=account_id, state='skipped', trigger='games')
        previous = await self.status(account_id, 'games')
        if (
            previous
            and previous.error_code == 'RateLimited'
            and previous.next_check_at
            and previous.next_check_at > now()
        ):
            raise RateLimited((previous.next_check_at - now()).total_seconds())
        try:
            run = await self._farming.run(
                account_id, target='games', trigger='bonus' if automatic else 'manual'
            )
        except RateLimited:
            # The failed run and cooldown were committed under the farming account lock.
            run = FarmRun(
                account_id=account_id, trigger='games', state='failed', error_code='RateLimited'
            )
        status = await self.status(account_id, 'games')
        if automatic and status:
            await self._notify(status, run)
        return run

    async def refresh_live(self, account_id: str) -> BonusStatus:
        """Refresh bootstrap only. Viewing a menu never submits progress."""
        async with self._locks.guard(validate_id(account_id)):
            account = await self._accounts.get(account_id)
            previous = await self.status(account_id, 'live')
            if (
                previous
                and previous.error_code == 'RateLimited'
                and previous.next_check_at
                and previous.next_check_at > now()
            ):
                return previous

            worker = asyncio.create_task(asyncio.to_thread(self._collector.read, account_id))
            try:
                cursor, cancelled = await settle(worker)
            except MarketError as exc:
                delay = max(60, exc.retry_after) if isinstance(exc, RateLimited) else 300
                status = BonusStatus(
                    account_id=account_id,
                    source='live',
                    state='failed',
                    error_code=type(exc).__name__,
                    next_check_at=now() + timedelta(seconds=delay),
                    balance=previous.balance if previous else None,
                    limit=previous.limit if previous else None,
                )
                if isinstance(exc, (AuthRequired, SessionError, AccessDenied)):
                    account.auth_state = 'blocked' if isinstance(exc, AccessDenied) else 'expired'
                    account.auto_collect = False
                    await self._accounts.save(account)
                await self._save_status(status, previous)
                return status
            # Do not replace the active polling cursor/timestamp merely because UI refreshed.
            status = self._collector.status(account_id, cursor)
            if previous:
                status.next_check_at = previous.next_check_at
                if previous.state in ('waiting_stream', 'stalled') and status.state == 'collecting':
                    status.state = previous.state
                    status.remaining_seconds = None
            await self._save_status(status, previous)
            if cancelled:
                raise asyncio.CancelledError
            return status

    async def _save_status(self, status: BonusStatus, previous: BonusStatus | None) -> None:
        if previous:
            status.cycle = previous.cycle
            status.notified_key = previous.notified_key
            if (
                status.balance is not None
                and previous.balance is not None
                and status.balance < previous.balance
            ):
                status.cycle += 1
                status.notified_key = None
            if (
                status.source == 'live'
                and status.state == 'collecting'
                and status.notified_key
                and status.notified_key.startswith('problem:')
                and previous.balance is not None
                and status.balance is not None
                and status.balance > previous.balance
            ):
                status.notified_key = None
        await self._runs.save(status)

    async def _prepare_notification(self, status: BonusStatus, run: FarmRun | None) -> bool:
        # Caller owns the account lock. Never merge an old status over a newer snapshot.
        latest = await self.status(status.account_id, status.source)
        if latest is None or latest.checked_at != status.checked_at:
            return False
        status.notified_key = latest.notified_key
        key = None
        if status.source == 'live' and status.state == 'limit':
            key = f'limit:{status.cycle}:{status.limit}'
        elif status.state in ('failed', 'uncertain', 'blocked', 'stalled'):
            key = f'problem:{status.state}:{status.error_code}'
        elif status.source == 'games' and run and run.state in ('success', 'partial'):
            operations = await self._runs.operations(run.account_id, run.id)
            if any(op.state in ('claimed', 'completed') for op in operations):
                key = 'reward:' + run.id
            elif run.state == 'partial':
                key = 'problem:partial'
        elif status.source == 'games' and status.state in ('success', 'no_rewards', 'reconciled'):
            if status.notified_key and status.notified_key.startswith('problem:'):
                status.notified_key = None
                await self._runs.save(status)
        if not key or key == status.notified_key:
            return False
        status.notified_key = key
        await self._runs.save(status)  # At most once per event, even after an uncertain delivery.
        return True

    async def _notify(self, status: BonusStatus, run: FarmRun | None) -> None:
        async with self._locks.guard(status.account_id):
            send = await self._prepare_notification(status, run)
        if send:
            await self._deliver(status, run)

    async def _deliver(self, status: BonusStatus, run: FarmRun | None) -> None:
        account = await self._accounts.get(status.account_id)
        if account.notifications and self._report:
            try:
                await self._report(status, run)
            except Exception as exc:
                logger.warning(
                    'Bonus notification failed: account={} source={} error={}',
                    status.account_id,
                    status.source,
                    type(exc).__name__,
                )

    async def tick_live(self, account_id: str) -> float:
        async with self._locks.guard(validate_id(account_id)):
            account = await self._accounts.get(account_id)
            if not self._active(account, 'live'):
                return 3600
            previous = await self.status(account_id, 'live')
            if previous and previous.next_check_at and previous.next_check_at > now():
                return (previous.next_check_at - now()).total_seconds()
            run = FarmRun(account_id=account_id, trigger='live')
            await self._runs.save(run)
            worker = asyncio.create_task(asyncio.to_thread(self._collector.poll, account_id, run))
            (status, delay, error), cancelled = await settle(worker)
            if error:
                run.error_code = type(error).__name__
                run.state = 'failed'
                self._collector.forget(account_id)
                if isinstance(error, (AuthRequired, SessionError, AccessDenied)):
                    account.auth_state = 'blocked' if isinstance(error, AccessDenied) else 'expired'
                    account.auto_collect = False
                status = BonusStatus(
                    account_id=account_id, source='live', state='failed', error_code=run.error_code
                )
                if previous:
                    status.balance, status.limit = previous.balance, previous.limit
                logger.warning(
                    'Live polling failed: account={} error={} retry_seconds={}',
                    account_id,
                    run.error_code,
                    delay,
                )
            status.next_check_at = now() + timedelta(seconds=delay)
            await self._save_status(status, previous)
            run.finished_at = now()
            await self._runs.save(account, run)
            if run.balance_after is not None:
                await self._runs.save(
                    BalanceSnapshot(account_id=account_id, run_id=run.id, coins=run.balance_after)
                )
            send = await self._prepare_notification(status, run)
        if send:
            await self._deliver(status, run)
        if cancelled:
            raise asyncio.CancelledError
        return delay

    async def stop(self) -> None:
        self._stopping = True
        if self._watcher:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._collector.cursors.clear()
