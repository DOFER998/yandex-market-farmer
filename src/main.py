import asyncio
import os

from loguru import logger

from src.common.utils import setup_logging
from src.lifecycle import run


def main() -> int:
    os.umask(0o077)
    setup_logging()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.error('Application stopped: {}', type(exc).__name__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
