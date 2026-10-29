"""Serialize menu/form transitions without blocking them on Market operations."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.callback_data.account import AccountAction, AccountCallback

LONG_ACTIONS = {
    AccountAction.BALANCE,
    AccountAction.COLLECT,
    AccountAction.COLLECT_GAMES,
    AccountAction.RESUME,
    AccountAction.QR_START,
    AccountAction.QR_SAVE,
}


class NavigationIsolation(BaseMiddleware):
    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._busy: set[int] = set()

    async def __call__(
        self, handler: Callable[..., Awaitable[Any]], event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        message = event.message if isinstance(event, CallbackQuery) else event
        if not isinstance(message, Message):
            return await handler(event, data)
        if message.chat.id in self._busy:
            if isinstance(event, CallbackQuery):
                await event.answer('Операция ещё выполняется. Дождись результата.')
            return None
        action = None
        long_action = False
        confirmation = False
        if isinstance(event, CallbackQuery) and event.data:
            try:
                action = AccountCallback.unpack(event.data).action
                long_action = action in LONG_ACTIONS
                confirmation = action == AccountAction.DELETE_CONFIRM
            except (ValueError, TypeError):
                pass
        lock = self._locks.setdefault(message.chat.id, asyncio.Lock())
        async with lock:
            if message.chat.id in self._busy:
                if isinstance(event, CallbackQuery):
                    await event.answer('Операция ещё выполняется. Дождись результата.')
                return None
            if data.get('qr_login') and action not in {
                AccountAction.QR_START,
                AccountAction.QR_SAVE,
            }:
                await data['qr_login'].cancel(message.chat.id)
            if isinstance(event, CallbackQuery) and not confirmation:
                await data['state'].clear()
            if not long_action:
                return await handler(event, data)
            self._busy.add(message.chat.id)
        try:
            return await handler(event, data)
        finally:
            self._busy.discard(message.chat.id)
