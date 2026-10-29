from collections.abc import Awaitable, Callable
from html import escape
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, ErrorEvent, Message, TelegramObject
from loguru import logger

from src.keyboards.accounts import back_keyboard
from src.market.errors import MarketError
from src.services.screens import ScreenService
from src.views.errors import error_text
from src.views.fragments import section


class ErrorMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except MarketError as exc:
            logger.warning('Operation rejected: {}', type(exc).__name__)
            text = escape(str(exc)) if type(exc) is MarketError else error_text(type(exc).__name__)
        except Exception as exc:
            logger.error('Handler failed: {}', type(exc).__name__)
            text = error_text(type(exc).__name__)
        message = event.message if isinstance(event, CallbackQuery) else event
        if isinstance(message, Message):
            screens: ScreenService = data['screens']
            revision = screens.request_revision(message)
            if revision is None:
                revision = screens.begin(message, callback=isinstance(event, CallbackQuery))
            await screens.show(
                message,
                section('Не удалось завершить действие', [text], icon='error', icons=screens.icons),
                back_keyboard(icons=screens.icons),
                revision,
            )
        return None


async def error_handler(event: ErrorEvent) -> bool:
    """Last boundary for failures while delivering a handler's response."""
    logger.error('Telegram handler failed: {}', type(event.exception).__name__)
    return True
