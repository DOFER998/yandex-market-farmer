from aiogram.enums import ChatType
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message


class IsPrivate(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        message = event.message if isinstance(event, CallbackQuery) else event
        return message is not None and message.chat.type == ChatType.PRIVATE
