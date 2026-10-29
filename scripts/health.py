"""Container healthcheck without creating a bot or making Market requests."""

import asyncio

from src.bootstrap import application
from src.common.config import Environment
from src.common.health import check_health


async def check() -> None:
    settings = Environment()
    async with application(settings) as app:
        await check_health(app.database.engine, settings.app.heartbeat_file)


if __name__ == '__main__':
    try:
        asyncio.run(check())
    except Exception:
        raise SystemExit(1) from None
