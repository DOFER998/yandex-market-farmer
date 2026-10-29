import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from src.common.config import Environment
from src.database import models  # noqa: F401

config = context.config
url = config.attributes.get('database_url') or Environment().postgres.dsn


def run(connection):
    context.configure(connection=connection, target_metadata=SQLModel.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = create_async_engine(
        url, hide_parameters=True, connect_args=config.attributes.get('connect_args', {})
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(url=url, target_metadata=SQLModel.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
