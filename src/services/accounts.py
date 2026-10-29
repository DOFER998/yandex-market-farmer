from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from uuid import uuid4

from src.common.time import now
from src.database.models import Account, FarmRun, Operation
from src.market.errors import MarketError
from src.market.models import DailyStatus
from src.market.session import SessionStore
from src.services.identity import validate_id
from src.services.ports import Accounts, Locks, Runs, RunStatistics
from src.services.reward_schedule import schedule_daily


@dataclass(frozen=True, slots=True)
class AccountHistory:
    runs: list[FarmRun]
    operations: list[Operation]


class AccountService:
    def __init__(
        self,
        accounts: Accounts,
        runs: Runs,
        locks: Locks,
        session_dir: Path,
        changed: Callable[[str], None] | None = None,
    ) -> None:
        self._changed = changed
        self._accounts = accounts
        self._runs = runs
        self._locks = locks
        self._session_dir = session_dir

    async def create(self, name: str) -> Account:
        name = name.strip()
        if not 1 <= len(name) <= 40 or any(ord(c) < 32 for c in name):
            raise MarketError('Название должно содержать от 1 до 40 символов без переносов строк.')
        account = Account(id='acc_' + uuid4().hex[:12], display_name=name)
        await self._accounts.save(account)
        return account

    async def delete(self, account_id: str) -> None:
        async with self._locks.guard(validate_id(account_id)):
            account = await self.get(account_id)
            # Fail closed if filesystem or database cleanup is interrupted.
            account.auto_collect = False
            account.auth_state = 'unknown'
            await self._accounts.save(account)
            if self._changed:
                self._changed(account_id)
            with SessionStore(self._session_dir / f'{account_id}.txt') as store:
                store.delete()
                await self._accounts.delete(account_id)
            if self._changed:
                self._changed(account_id)

    async def rename(self, account_id: str, name: str) -> Account:
        name = name.strip()
        if not 1 <= len(name) <= 40 or any(ord(c) < 32 for c in name):
            raise MarketError('Название должно содержать от 1 до 40 символов без переносов строк.')
        async with self._locks.guard(validate_id(account_id)):
            account = await self.get(account_id)
            account.display_name = name
            await self._accounts.save(account)
            if self._changed:
                self._changed(account.id)
            return account

    async def latest_run(self, account_id: str) -> FarmRun | None:
        runs = await self._runs.history(validate_id(account_id), limit=1)
        return runs[0] if runs else None

    async def list(self) -> list[Account]:
        return await self._accounts.list()

    async def get(self, account_id: str) -> Account:
        return await self._accounts.get(validate_id(account_id))

    async def run_operations(self, run: FarmRun) -> list[Operation]:
        return await self._runs.operations(validate_id(run.account_id), run_id=run.id)

    async def history(self, account_id: str) -> AccountHistory:
        await self.get(account_id)
        return AccountHistory(
            await self._runs.history(account_id, limit=5),
            (await self._runs.operations(account_id))[:10],
        )

    async def statistics(self, account_id: str) -> RunStatistics:
        await self.get(account_id)
        return await self._runs.statistics(account_id)

    async def set_notifications(self, account_id: str, enabled: bool) -> Account:
        async with self._locks.guard(validate_id(account_id)):
            account = await self.get(account_id)
            account.notifications = enabled
            await self._accounts.save(account)
            if self._changed:
                self._changed(account.id)
            return account

    async def import_session(self, account_id: str, source: Path, *, create: bool) -> None:
        async with self._locks.guard(validate_id(account_id)):
            if create:
                if any(account.id == account_id for account in await self.list()):
                    raise MarketError('Account already exists; use account-session')
                account = Account(id=account_id)
            else:
                account = await self.get(account_id)
            with SessionStore(self._session_dir / f'{account_id}.txt') as store:
                store.import_file(source)
            account.auth_state = 'unknown'
            account.auto_collect = False
            await self._accounts.save(account)
            if self._changed:
                self._changed(account.id)

    async def attach_verified(self, account_id: str, jar: CookieJar, balance: int) -> Account:
        async with self._locks.guard(validate_id(account_id)):
            account = await self.get(account_id)
            # Stop automation before replacing credentials; never enable it implicitly.
            account.auto_collect = False
            account.auth_state = 'unknown'
            await self._accounts.save(account)
            if self._changed:
                self._changed(account_id)
            with SessionStore(self._session_dir / f'{account_id}.txt') as store:
                store.save(jar)
            account.auth_state = 'ready'
            account.daily_available = None
            account.daily_reward_at = None
            account.next_run_at = now()
            run = FarmRun(
                account_id=account_id,
                trigger='read',
                state='success',
                balance_before=balance,
                balance_after=balance,
                finished_at=now(),
            )
            await self._runs.save(account, run)
            if self._changed:
                self._changed(account_id)
            return account

    async def configure(
        self, account_id: str, *, enabled: bool | None = None, minutes: int | None = None
    ) -> Account:
        validate_id(account_id)
        async with self._locks.guard(account_id):
            account = await self._accounts.get(account_id)
            await self._runs.recover(account)
            if enabled and (account.needs_review or account.auth_state != 'ready'):
                raise MarketError('Сначала нажми «Проверить вход», затем включи автосбор.')
            if minutes is not None:
                if not 15 <= minutes <= 1440:
                    raise MarketError('Interval must be 15..1440 minutes')
                account.interval_minutes = minutes
            if enabled is not None:
                account.auto_collect = enabled
            if enabled and account.next_run_at <= now():
                if account.daily_available is not None:
                    schedule_daily(
                        account,
                        DailyStatus(
                            available=account.daily_available,
                            next_reward_at=account.daily_reward_at,
                        ),
                    )
                else:
                    account.next_run_at = now()
            await self._runs.save(account)
            if self._changed:
                self._changed(account.id)
            return account
