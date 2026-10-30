import asyncio
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Literal

from loguru import logger

from src.common.tasks import settle
from src.common.time import now
from src.database.models import FarmRun
from src.services.identity import validate_id
from src.services.journal import OperationJournal
from src.services.ports import Accounts, ClientFactory, Locks, Runs
from src.services.worker import FarmingWorker


class FarmingService:
    def __init__(
        self,
        accounts: Accounts,
        runs: Runs,
        locks: Locks,
        session_dir: Path,
        *,
        factory: ClientFactory,
        changed: Callable[[str], None] | None = None,
    ) -> None:
        self._changed = changed
        self._accounts = accounts
        self._runs = runs
        self._locks = locks
        self.session_dir = session_dir
        self.factory = factory

    async def run(
        self,
        account_id: str,
        *,
        trigger: str = 'manual',
        target: Literal['daily', 'games'] = 'daily',
        read_only: bool = False,
        reconcile: bool = False,
    ) -> FarmRun:
        if target not in ('daily', 'games') or (
            target == 'games' and (read_only or trigger == 'schedule')
        ):
            raise ValueError('Invalid collection target')
        validate_id(account_id)
        async with self._locks.guard(account_id):
            account = await self._accounts.get(account_id)
            await self._runs.recover(account)
            if not read_only and account.needs_review:
                return FarmRun(account_id=account_id, state='blocked', error_code='ReviewRequired')
            if trigger == 'schedule' and (not account.auto_collect or account.next_run_at > now()):
                return FarmRun(account_id=account_id, state='skipped')
            run = FarmRun(
                account_id=account_id,
                trigger='read' if read_only else ('games' if target == 'games' else trigger),
            )
            if not read_only and target == 'daily' and account.next_run_at <= now():
                account.next_run_at = now() + timedelta(minutes=5)
            await self._runs.save(account, run)
            journal = OperationJournal(self._runs, asyncio.get_running_loop(), run)
            execution = FarmingWorker(self.factory, self.session_dir)
            worker = asyncio.create_task(
                asyncio.to_thread(execution.execute, account, run, journal, read_only)
            )
            _, cancelled = await settle(worker)
            if cancelled:
                # Leave durable run as running; next acquisition requires reconciliation.
                raise asyncio.CancelledError
            run.finished_at = now()
            if reconcile and read_only and run.state == 'success':
                account.needs_review = False
            await self._runs.save(account, run)
            if self._changed:
                self._changed(account_id)
            logger.info(
                'Collection finished: account={} run={} state={} error={}',
                account_id,
                run.id,
                run.state,
                run.error_code,
            )
            return run
