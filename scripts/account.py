"""Import a locally captured session into a managed account."""

import argparse
import asyncio
import os
from pathlib import Path

from loguru import logger

from src.bootstrap import application
from src.common.config import Environment
from src.common.utils import setup_logging


async def import_account(account: str, source: Path, *, replace: bool) -> None:
    async with application(Environment()) as app:
        await app.accounts.import_session(account, source, create=not replace)
    logger.info(
        f'Account {account}: session imported; auto disabled. В боте: Подключение → Проверить вход'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('account')
    parser.add_argument('source', type=Path)
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    setup_logging()
    try:
        asyncio.run(import_account(args.account, args.source, replace=args.replace))
        return 0
    except Exception as exc:
        logger.error(
            f'{type(exc).__name__}: session import failed; check account, database and source'
        )
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
