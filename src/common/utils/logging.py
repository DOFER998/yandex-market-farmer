"""One logging pipeline for application and library records."""

import logging
import sys

from loguru import logger


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # Third-party exceptions may contain URLs, headers or complete updates.
        # Keep error types, never render their payloads or frame locals.
        if record.exc_info and record.exc_info[0]:
            message = f'{record.name}: {record.exc_info[0].__name__}'
        else:
            message = record.getMessage()
        logger.opt(depth=2).log(record.levelname, message)


def setup_logging(*, debug: bool = False) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level='DEBUG' if debug else 'INFO',
        format='{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}',
        backtrace=False,
        diagnose=False,
    )
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.WARNING, force=True)
    # HTTP debug output must not expose authorization headers or session cookies.
    for name in ('aiogram', 'apscheduler', 'sqlalchemy', 'curl_cffi'):
        logging.getLogger(name).setLevel(logging.WARNING)
