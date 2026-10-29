import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

from src.database.models import Account
from src.database.session import Database


def test_migration_roundtrip_and_sqlmodel_compatibility(tmp_path):
    url = 'sqlite+aiosqlite:///' + str(tmp_path / 'migration.db')
    config = Config('alembic.ini')
    config.attributes['database_url'] = url
    command.upgrade(config, 'head')
    command.check(config)

    async def verify():
        engine = create_async_engine(url)
        db = Database(engine)
        await db.accounts.save(Account(id='test'))
        account = await db.accounts.get('test')
        assert account.next_run_at.utcoffset().total_seconds() == 0
        assert account.notifications and not account.auto_collect
        await engine.dispose()

    asyncio.run(verify())
    command.downgrade(config, 'base')
    command.upgrade(config, 'head')


def test_name_migration_preserves_existing_accounts(tmp_path):
    from sqlalchemy import text

    url = 'sqlite+aiosqlite:///' + str(tmp_path / 'existing.db')
    config = Config('alembic.ini')
    config.attributes['database_url'] = url
    command.upgrade(config, '0001')

    async def seed():
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text("""INSERT INTO accounts
                    (id, auth_state, auto_collect, notifications, interval_minutes,
                     next_run_at, needs_review, created_at)
                    VALUES ('main', 'ready', 1, 1, 360,
                            '2026-10-29 12:00:00', 0, '2026-10-29 12:00:00')""")
                )
        finally:
            await engine.dispose()

    asyncio.run(seed())
    command.upgrade(config, 'head')

    async def verify():
        engine = create_async_engine(url)
        try:
            account = await Database(engine).accounts.get('main')
            assert account.id == 'main'
            assert account.display_name == 'main'
            assert account.auth_state == 'ready'
            assert account.auto_collect
        finally:
            await engine.dispose()

    asyncio.run(verify())
