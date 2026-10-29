from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message


class IsOwner(Filter):
    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user is not None and event.from_user.id == self.owner_id
