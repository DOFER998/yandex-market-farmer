from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from src.database.locks import AccountLocks
from src.database.repositories import AccountRepository, RunRepository


class Database:
    """Composition of persistence resources; no queries or domain rules here."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        self.accounts = AccountRepository(self.session)
        self.runs = RunRepository(self.session)
        self.locks = AccountLocks(engine)
