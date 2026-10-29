from datetime import datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel

from src.common.time import now


def uid() -> str:
    return str(uuid4())


class Account(SQLModel, table=True):
    __tablename__ = 'accounts'
    id: str = Field(primary_key=True, max_length=32)
    display_name: str = Field(default='', max_length=40)
    auth_state: str = Field(default='unknown', max_length=24)
    auto_collect: bool = False
    notifications: bool = True
    interval_minutes: int = Field(default=360, ge=15, le=1440)
    daily_reward_at: datetime | None = None
    daily_available: bool | None = None
    next_run_at: datetime = Field(default_factory=now, index=True)
    needs_review: bool = False
    created_at: datetime = Field(default_factory=now)


class FarmRun(SQLModel, table=True):
    __tablename__ = 'farm_runs'
    id: str = Field(default_factory=uid, primary_key=True, max_length=36)
    account_id: str = Field(foreign_key='accounts.id', index=True)
    trigger: str = Field(default='manual', max_length=24)
    state: str = Field(default='running', max_length=24)
    started_at: datetime = Field(default_factory=now, index=True)
    finished_at: datetime | None = None
    balance_before: int | None = None
    balance_after: int | None = None
    error_code: str | None = Field(default=None, max_length=80)


class Operation(SQLModel, table=True):
    __tablename__ = 'operations'
    id: str = Field(default_factory=uid, primary_key=True, max_length=36)
    run_id: str = Field(foreign_key='farm_runs.id', index=True)
    account_id: str = Field(foreign_key='accounts.id', index=True)
    kind: str = Field(max_length=80)
    state: str = Field(default='pending', max_length=40)
    created_at: datetime = Field(default_factory=now, index=True)
    error_code: str | None = Field(default=None, max_length=80)


class BalanceSnapshot(SQLModel, table=True):
    __tablename__ = 'balance_snapshots'
    id: str = Field(default_factory=uid, primary_key=True, max_length=36)
    account_id: str = Field(foreign_key='accounts.id', index=True)
    run_id: str = Field(foreign_key='farm_runs.id')
    coins: int = Field(ge=0)
    created_at: datetime = Field(default_factory=now, index=True)
