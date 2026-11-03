"""Retry polling safely; never duplicate a message after an uncertain delivery."""

import asyncio

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageCaption,
    EditMessageMedia,
    EditMessageReplyMarkup,
    EditMessageText,
    GetUpdates,
)
from loguru import logger

from src.common.utils.errors import error_details


class TelegramResilience(BaseRequestMiddleware):
    def __init__(self, *, sleep=asyncio.sleep):
        self._sleep = sleep

    async def __call__(self, make_request, bot, method):
        polling = isinstance(method, GetUpdates)
        failures = 0
        while True:
            try:
                result = await make_request(bot, method)
                if failures:
                    logger.info(
                        'Telegram recovered: method={} attempts={}', type(method).__name__, failures
                    )
                return result
            except TelegramBadRequest as exc:
                if (
                    isinstance(
                        method,
                        (
                            EditMessageText,
                            EditMessageCaption,
                            EditMessageMedia,
                            EditMessageReplyMarkup,
                        ),
                    )
                    and 'message is not modified' in exc.message.lower()
                ):
                    # ScreenService treats this as successful idempotent rendering.
                    raise
                if isinstance(method, AnswerCallbackQuery) and (
                    'query is too old' in exc.message.lower()
                    or 'query id is invalid' in exc.message.lower()
                ):
                    logger.debug('Telegram callback acknowledgement expired; continuing navigation')
                    return True
                logger.warning('Telegram rejected request: {}', error_details(exc))
                raise
            except (TelegramRetryAfter, TelegramNetworkError, TelegramServerError) as exc:
                failures += 1
                delay = (
                    exc.retry_after
                    if isinstance(exc, TelegramRetryAfter)
                    else min(2 ** min(failures, 5), 30)
                )
                retry = polling or (
                    isinstance(exc, TelegramRetryAfter) and failures <= 2 and delay <= 30
                )
                if failures == 1 or failures % 10 == 0 or not retry:
                    logger.warning(
                        'Telegram unavailable: method={} error={} attempt={} '
                        'retry={} wait_seconds={}',
                        type(method).__name__,
                        type(exc).__name__,
                        failures,
                        retry,
                        delay if retry else 0,
                    )
                if not retry:
                    raise
                await self._sleep(delay)
            except TelegramAPIError as exc:
                logger.warning(
                    'Telegram request failed: method={} error={}',
                    type(method).__name__,
                    type(exc).__name__,
                )
                raise
