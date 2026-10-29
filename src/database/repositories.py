from collections.abc import Callable

from sqlalchemy import delete, func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.common.time import now
from src.database.models import Account, BalanceSnapshot, FarmRun, Operation
from src.services.errors import AccountNotFound
from src.services.ports import RunStatistics

SessionFactory = Callable[[], AsyncSession]


class AccountRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self.session = session_factory

    async def save(self, account: Account) -> None:
        async with self.session() as session:
            session.add(account)
            await session.commit()

    async def get(self, account_id: str) -> Account:
        async with self.session() as session:
            account = await session.get(Account, account_id)
            if account is None:
                raise AccountNotFound('Unknown account')
            return account

    async def list(self) -> list[Account]:
        async with self.session() as session:
            return list((await session.exec(select(Account).order_by(Account.id))).all())

    async def delete(self, account_id: str) -> None:
        async with self.session() as session:
            async with session.begin():
                for model in (Operation, BalanceSnapshot, FarmRun, Account):
                    column = model.id if model is Account else model.account_id
                    await session.exec(delete(model).where(column == account_id))


class RunRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self.session = session_factory

    async def save(self, *rows: Account | FarmRun | Operation | BalanceSnapshot) -> None:
        async with self.session() as session:
            for row in rows:
                session.add(row)
            await session.commit()

    async def history(self, account_id: str, limit: int = 10) -> list[FarmRun]:
        async with self.session() as session:
            return list(
                (
                    await session.exec(
                        select(FarmRun)
                        .where(FarmRun.account_id == account_id)
                        .order_by(FarmRun.started_at.desc())
                        .limit(limit)
                    )
                ).all()
            )

    async def operations(self, account_id: str, run_id: str | None = None) -> list[Operation]:
        query = select(Operation).where(Operation.account_id == account_id)
        if run_id is not None:
            query = query.where(Operation.run_id == run_id)
        query = query.order_by(Operation.created_at.desc())
        if run_id is None:
            query = query.limit(50)
        async with self.session() as session:
            return list((await session.exec(query)).all())

    async def recover(self, account: Account) -> bool:
        async with self.session() as session:
            runs = (
                await session.exec(
                    select(FarmRun).where(
                        FarmRun.account_id == account.id, FarmRun.state == 'running'
                    )
                )
            ).all()
            for run in runs:
                run.state = 'interrupted'
                run.finished_at = now()
                run.error_code = 'ProcessInterrupted'
                session.add(run)
            if runs:
                account.needs_review = True
                account.auto_collect = False
                session.add(account)
                await session.commit()
            return bool(runs)

    async def statistics(self, account_id: str) -> RunStatistics:
        async with self.session() as session:
            count = (
                await session.exec(
                    select(func.count())
                    .select_from(FarmRun)
                    .where(FarmRun.account_id == account_id, FarmRun.trigger != 'read')
                )
            ).one()
            failures = (
                await session.exec(
                    select(func.count())
                    .select_from(FarmRun)
                    .where(
                        FarmRun.account_id == account_id,
                        FarmRun.state.in_(['failed', 'uncertain', 'interrupted']),
                    )
                )
            ).one()
            return RunStatistics(collections=count, failures=failures)
