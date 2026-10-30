"""Opt-in real PostgreSQL verification in an isolated temporary schema."""

import asyncio
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.common.config import Environment
from src.database.locks import AccountLocks, Busy
from src.database.models import Account, FarmRun
from src.database.session import Database

pytestmark = pytest.mark.skipif(
    os.environ.get('YMF_TEST_POSTGRES') != '1', reason='Run make test-postgres with local Docker'
)


def test_migrations_repositories_and_cross_connection_lock():
    url = Environment().postgres.dsn
    schema = 'test_' + uuid4().hex
    connect_args = {'server_settings': {'search_path': schema}}

    async def schema_command(statement):
        engine = create_async_engine(url, hide_parameters=True)
        try:
            async with engine.begin() as connection:
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    async def verify():
        engine = create_async_engine(url, connect_args=connect_args, hide_parameters=True)
        try:
            db = Database(engine)
            await db.accounts.save(Account(id='integration'))
            await db.runs.save(FarmRun(account_id='integration'))
            account = await db.accounts.get('integration')
            assert await db.runs.recover(account)
            assert (await db.accounts.get('integration')).daily_needs_review
            assert (await db.runs.history('integration'))[0].state == 'interrupted'
            # Separate lock managers ensure PostgreSQL, not the in-process lock, excludes us.
            other = AccountLocks(engine)
            async with db.locks.guard(schema):
                with pytest.raises(Busy):
                    async with other.guard(schema):
                        pytest.fail('Concurrent lock acquired')
            async with other.guard(schema):
                pass
        finally:
            await engine.dispose()

    asyncio.run(schema_command(f'CREATE SCHEMA "{schema}"'))
    try:
        config = Config('alembic.ini')
        config.attributes.update(database_url=url, connect_args=connect_args)
        command.upgrade(config, 'head')
        asyncio.run(verify())
        command.downgrade(config, 'base')
        command.upgrade(config, 'head')
    finally:
        asyncio.run(schema_command(f'DROP SCHEMA "{schema}" CASCADE'))
