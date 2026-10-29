"""Prepare .env interactively; credentials never appear in terminal output."""

import os
import secrets
from getpass import getpass
from pathlib import Path

from loguru import logger

from src.common.utils import setup_logging


def main() -> None:
    os.umask(0o077)
    setup_logging()
    if os.getuid() == 0:
        raise SystemExit('Run setup as the non-root user that will own the session files')
    folder = Path('.secrets')
    folder.mkdir(mode=0o700, exist_ok=True)
    (folder / 'accounts').mkdir(mode=0o700, exist_ok=True)
    env = Path('.env')
    if env.exists():
        logger.info('.env already exists; keeping its contents. Compare with .env.example')
        return
    owner = int(input('Telegram user ID владельца: '))
    if owner <= 0:
        raise ValueError('ID must be positive')
    token = getpass('Токен Telegram-бота (ввод скрыт): ').strip()
    if not token or any(c in token for c in '\r\n\'"\\'):
        raise ValueError('Invalid bot token')
    password = secrets.token_hex(24)
    with env.open('x') as output:
        output.write(
            f"BOT__TOKEN='{token}'\nBOT__OWNER_ID={owner}\n"
            'POSTGRES__HOST=127.0.0.1\nPOSTGRES__PORT=15432\nPOSTGRES__USER=ymf\n'
            f'POSTGRES__PASSWORD={password}\nPOSTGRES__DATABASE=ymf\n'
            'APP__DEBUG=false\nAPP__HEARTBEAT_FILE=.secrets/runtime/heartbeat\n'
            'MARKET__SESSION_DIR=.secrets/accounts\nMARKET__INTERVAL_SECONDS=2\n'
            f'YMF_UID={os.getuid()}\nYMF_GID={os.getgid()}\n'
        )
    logger.info('.env created. Next: make db-up migrate')


if __name__ == '__main__':
    main()
