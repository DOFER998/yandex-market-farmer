from aiogram import Dispatcher, Router

from src.filters.owner import IsOwner
from src.filters.private import IsPrivate
from src.middlewares.errors import ErrorMiddleware
from src.middlewares.navigation import NavigationIsolation
from src.routers.private import get_routers


def include_routers(dispatcher: Dispatcher, *, owner_id: int) -> None:
    private = Router(name='owner_private')
    private.message.filter(IsPrivate(), IsOwner(owner_id))
    private.callback_query.filter(IsPrivate(), IsOwner(owner_id))
    navigation = NavigationIsolation()
    private.message.middleware(navigation)
    private.callback_query.middleware(navigation)
    private.message.middleware(ErrorMiddleware())
    private.callback_query.middleware(ErrorMiddleware())
    private.include_routers(*get_routers())
    dispatcher.include_router(private)
