import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, GetUpdates, SendMessage

from src.middlewares.telegram import TelegramResilience


def test_polling_honors_retry_after_and_recovers():
    async def scenario():
        method = GetUpdates()
        request = AsyncMock(
            side_effect=[TelegramRetryAfter(method=method, message='limit', retry_after=5), []]
        )
        sleep = AsyncMock()
        result = await TelegramResilience(sleep=sleep)(request, None, method)
        assert result == []
        sleep.assert_awaited_once_with(5)

    asyncio.run(scenario())


def test_delivery_timeout_is_not_blindly_retried():
    async def scenario():
        method = SendMessage(chat_id=1, text='private')
        request = AsyncMock(side_effect=TelegramNetworkError(method=method, message='timeout'))
        with pytest.raises(TelegramNetworkError):
            await TelegramResilience()(request, None, method)
        assert request.await_count == 1

    asyncio.run(scenario())


def test_expired_callback_ack_does_not_abort_navigation():
    async def scenario():
        method = AnswerCallbackQuery(callback_query_id='private')
        request = AsyncMock(
            side_effect=TelegramBadRequest(
                method=method,
                message='query is too old and response timeout expired or query ID is invalid',
            )
        )
        assert await TelegramResilience()(request, None, method) is True

    asyncio.run(scenario())


def test_unchanged_edit_reaches_screen_handler_without_warning(monkeypatch):
    from unittest.mock import Mock

    from aiogram.methods import EditMessageText

    from src.middlewares import telegram

    async def scenario():
        warning = Mock()
        monkeypatch.setattr(telegram.logger, 'warning', warning)
        method = EditMessageText(chat_id=1, message_id=1, text='same')
        request = AsyncMock(
            side_effect=TelegramBadRequest(
                method=method, message='Bad Request: message is not modified'
            )
        )
        with pytest.raises(TelegramBadRequest):
            await TelegramResilience()(request, None, method)
        warning.assert_not_called()
        request.side_effect = TelegramBadRequest(
            method=method, message='Bad Request: chat not found'
        )
        with pytest.raises(TelegramBadRequest):
            await TelegramResilience()(request, None, method)
        warning.assert_called_once()

    asyncio.run(scenario())
