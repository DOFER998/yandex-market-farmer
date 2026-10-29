import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from loguru import logger

from src.common.tasks import settle


class UpdateTasks(BaseMiddleware):
    """Drain concurrent updates before their database and HTTP resources close."""

    def __init__(self) -> None:
        self.active: set[asyncio.Task] = set()
        self.closing = False

    async def __call__(
        self, handler: Callable[..., Awaitable[Any]], event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        if self.closing:
            return None
        task = asyncio.current_task()
        self.active.add(task)
        started = asyncio.get_running_loop().time()
        try:
            return await handler(event, data)
        finally:
            self.active.discard(task)
            logger.debug('Update completed in {:.3f}s', asyncio.get_running_loop().time() - started)

    async def close(self) -> None:
        self.closing = True
        if self.active:
            draining = asyncio.ensure_future(
                asyncio.gather(*tuple(self.active), return_exceptions=True)
            )
            _, cancelled = await settle(draining)
            if cancelled:
                raise asyncio.CancelledError
