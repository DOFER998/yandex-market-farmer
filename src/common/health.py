import asyncio
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        while True:
            temp = path.with_suffix('.tmp')
            temp.write_text(str(time.time()))
            temp.chmod(0o600)
            temp.replace(path)
            await asyncio.sleep(15)
    finally:
        path.unlink(missing_ok=True)


async def check_health(engine: AsyncEngine, heartbeat_file: Path) -> None:
    stamp = float(heartbeat_file.read_text())
    if not 0 <= time.time() - stamp < 90:
        raise ValueError('Stale heartbeat')
    async with engine.connect() as connection:
        await connection.execute(text('SELECT 1'))
